"""
Custom exceptions extending those definined by DRF.
"""
from rest_framework import status
from rest_framework.exceptions import APIException

from .constants import ENROLLMENT_WRITE_MAX_SIZE, UPLOAD_FILE_MAX_SIZE


class EnrollmentPayloadTooLarge(APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = f'Enrollment limit {ENROLLMENT_WRITE_MAX_SIZE}'
    default_code = 'payload_too_large'


class FileTooLarge(APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = f'Upload too large. Must be under {UPLOAD_FILE_MAX_SIZE} bytes'
    default_code = 'file_too_large'
