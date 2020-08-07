""" Utilities for storing files in a backend-agnostic way. """

import logging
import posixpath

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, get_storage_class

from registrar.apps.api.utils import to_absolute_api_url


logger = logging.getLogger(__name__)


class FilestoreBase:
    """
    Abstract base class for file stores.
    """
    def __init__(self, backend, bucket, path_prefix):
        self.backend = backend
        self.bucket = bucket
        self.path_prefix = path_prefix

    def store(self, path, contents):
        """
        Store a file.

        Arguments:
            path: Path to file within filestore.
                Will be prefixed by `self.path_prefix`.
            contents: Contents of file.

        Returns: str
            URL to result
        """
        to_save = ContentFile(bytes(contents, 'utf-8'))
        self._try_with_error_logging(lambda p: self.backend.save(p, to_save), "saving to", path)
        return self.get_url(path)

    def retrieve(self, path):
        """
        Retrieve the contents of a file.

        Arguments:
            path: Path to file within filestore.
                Will be prefixed by `self.path_prefix`.

        Returns: str
            UTF-8 decoded file contents.
        """
        full_path = self.get_full_path(path)
        try:
            with self.backend.open(full_path, 'r') as f:
                content = f.read()
                return content if isinstance(content, str) else content.decode('utf-8')
        except OSError as e:
            logger.exception(
                "Could not read file stored at path {!r} in bucket {!r}: {}".format(
                    full_path, self.bucket, e
                )
            )
            return None

    def delete(self, path):
        """
        Delete a file.

        Arguments:
            path: Path to file within filestore.
                Will be prefixed by `self.path_prefix`.
        """
        self._try_with_error_logging(self.backend.delete, "deleting", path)

    def exists(self, path):
        """
        Check whether a file exists.

        Arguments:
            path: Path to file within filestore.
                Will be prefixed by `self.path_prefix`.

        Returns: bool
        """
        return self._try_with_error_logging(self.backend.exists, "checking existence of", path)

    def list(self, path):
        """
        List the contents of a specified path.

        Arguents:
            path: Path to file or directory.
                Will be prefixed by `self.path_prefix`.

        Returns:
            2-tuple of lists; the first item being directories, the second item being files
        """
        return self._try_with_error_logging(
            self.backend.listdir, "listing contents of", path
        )

    def get_url(self, path):
        """
        Given the path of a file in the store, return a URL to the file.
        Path will be prefixed by `self.path_prefix`.
        """
        return self._try_with_error_logging(self.backend.url, "getting URL of", path)

    def get_full_path(self, path):
        """
        Apply `self.path_prefix` to `path`. Use POSIX path joining.
        """
        return posixpath.join(self.path_prefix, path)

    def _try_with_error_logging(self, operation, operation_description, path):
        """
        Attempt an operation on a (relative) path.

        If it fails with a Boto client error, log the path and bucket name,
        then re-raise that exception.

        Arguments:
            operation: str -> T
            operation_description: str
            path: str

        Returns: T
        """
        full_path = self.get_full_path(path)
        try:
            return operation(full_path)
        except ClientError:
            logger.error(
                "Error while {} {!r} in bucket {!r}.".format(
                    operation_description, full_path, self.bucket
                )
            )
            raise


class FileSystemFilestore(FilestoreBase):
    """
    File storage using Django's FileStorageBackend.
    """
    def __init__(self, bucket, path_prefix):
        prefix_with_bucket = posixpath.join(bucket, path_prefix)
        super().__init__(default_storage, bucket, prefix_with_bucket)

    def get_url(self, path):
        """
        Given the path of a file in the store, return a URL to the file.
        Path will be prefixed by `self.path_prefix`.
        """
        return to_absolute_api_url(settings.MEDIA_URL, self.get_full_path(path))  # pragma: no cover


class S3Filestore(FilestoreBase):
    """
    File storage using S3Boto3Storage.
    """
    def __init__(self, bucket, path_prefix):
        storage_backend = get_storage_class()(bucket_name=bucket)
        super().__init__(storage_backend, bucket, path_prefix)


def get_enrollment_uploads_filestore():
    """
    Get filestore instance for uploaded enrollment CSVs.
    """
    return get_filestore(settings.REGISTRAR_BUCKET, 'uploads')


def get_job_results_filestore():
    """
    Get filestore instance for job results.
    """
    return get_filestore(settings.REGISTRAR_BUCKET, 'job-results')


def get_program_reports_filestore():
    """
    Get filestore instance for program analytics reports.
    """
    return get_filestore(settings.PROGRAM_REPORTS_BUCKET, settings.PROGRAM_REPORTS_FOLDER)


def get_filestore(bucket, path_prefix):
    """
    Return an instance of a FilestoreBase subclass, based on the
    configured default storage backend.
    """
    class_name = get_storage_class().__name__
    if class_name == 'FileSystemStorage':  # pragma: no cover
        return FileSystemFilestore(bucket, path_prefix)
    elif class_name == 'S3Boto3Storage':
        return S3Filestore(bucket, path_prefix)
    else:  # pragma: no cover
        raise ImproperlyConfigured(
            f'Unsupported storage backend for filestore: {class_name}'
        )
