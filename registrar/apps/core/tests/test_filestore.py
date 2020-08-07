""" Tests for core/filestore.py """

from itertools import product
from pathlib import PurePath
from unittest import mock

import boto3
import ddt
import moto
import requests
from botocore.exceptions import ClientError
from django.test import TestCase

from ..filestore import (
    FilestoreBase,
    FileSystemFilestore,
    S3Filestore,
    get_enrollment_uploads_filestore,
    get_filestore,
    get_job_results_filestore,
    get_program_reports_filestore,
)
from ..filestore import logger as filestore_logger
from .mixins import S3MockEnvVarsMixin


@ddt.ddt
class S3FilestoreTests(TestCase, S3MockEnvVarsMixin):
    """
    Tests for S3Filestore, which is the default Filestore under test settings.
    """
    test_bucket_1 = 'test-bucket1'
    test_bucket_2 = 'test-bucket2'

    bucket_variants = (test_bucket_1, test_bucket_2)
    location_variants = ('', 'bucketprefix/')
    prefix_variants = ("", "prefix", "prefix/withslashes/")
    path_variants = ("file.txt", "folder/file.txt")
    contents_variants = ("filecontents!", "")

    def setUp(self):
        # This is unfortunately duplicated from:
        #   registrar.apps.api.v1.tests.test_views:S3MockMixin.
        # It would be ideal to move that mixin to a utilities file and re-use
        # it here, but moto seems to have a bug/"feature" where it only works
        # in modules that explicitly import it.
        super().setUp()
        self._s3_mock = moto.mock_s3()
        self._s3_mock.start()
        conn = boto3.resource('s3')
        conn.create_bucket(Bucket=self.test_bucket_1)
        conn.create_bucket(Bucket=self.test_bucket_2)

    def tearDown(self):
        self._s3_mock.stop()
        super().tearDown()

    @ddt.data(
        *product(
            bucket_variants,
            location_variants,
            prefix_variants,
            path_variants,
            contents_variants,
        )
    )
    @ddt.unpack
    def test_s3_filestore(self, bucket, location, prefix, path, contents):
        filestore = get_filestore(bucket, prefix)
        with mock.patch.object(filestore.backend, 'location', new=location):
            url = filestore.store(path, contents)
            self.assertTrue(filestore.exists(path))
            self.assertIn(location, url)

            pure_path = PurePath(path)
            parent_path = pure_path.parent.name
            file_name = pure_path.name

            files = filestore.list(parent_path)[1]
            self.assertEqual(files, [file_name])

            response = requests.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text, contents)
            retrieved = filestore.retrieve(path)
            self.assertEqual(retrieved, contents)
            filestore.delete(path)
            self.assertFalse(filestore.exists(path))

    @mock.patch.object(filestore_logger, 'exception', autospec=True)
    def test_s3_filestore_not_found(self, mock_log_exception):
        filestore = get_filestore(self.test_bucket_1, "prefix")
        with mock.patch.object(filestore.backend, 'location', new="bucketprefix/"):
            retrieved = filestore.retrieve("file.txt")
            self.assertIsNone(retrieved)
            mock_log_exception.assert_called_once()

    def test_s3_noop_delete(self):
        filestore = get_filestore(self.test_bucket_1, "")
        self.assertFalse(filestore.exists("x.txt"))
        filestore.delete("x.txt")


@ddt.ddt
class FilestoreTests(TestCase):
    """
    Basic tests for Filestores in general.
    """
    @ddt.data(
        get_enrollment_uploads_filestore,
        get_job_results_filestore,
        get_program_reports_filestore,
    )
    def test_get_filestores(self, get_filestore_function):
        filestore = get_filestore_function()
        assert isinstance(filestore, FilestoreBase)

    @ddt.data(FileSystemFilestore, S3Filestore)
    def test_initialize_filestore_classes(self, filestore_class):
        filestore_class('test-bucket', 'test/path')

    def test_filestore_error_logging(self):
        bucket = 'fake-bucket'
        path_prefix = 'fake/prefix'
        filepath = 'faker/file.csv'
        contents = 'ThisIsPotentiallyPII'
        filestore = FileSystemFilestore(bucket, path_prefix)

        log_error_patcher = mock.patch.object(filestore_logger, 'error', autospec=True)
        save_patcher = mock.patch.object(
            filestore.backend, 'save', autospec=True, side_effect=ClientError({}, 'fake-error')
        )
        with log_error_patcher as mock_log_error, save_patcher as _mock_save:
            with self.assertRaises(ClientError):
                filestore.store(filepath, contents)

        log_message = mock_log_error.call_args_list[0][0][0]
        assert bucket in log_message
        assert path_prefix in log_message
        assert filepath in log_message
        assert contents not in log_message
