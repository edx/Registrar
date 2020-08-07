""" Constants for the core app. """


class Status:
    """Health statuses."""
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"


# Pulled from edx-platform. Will correctly capture both old- and new-style
# course ID strings.
INTERNAL_COURSE_KEY_PATTERN = r'([^/+]+(/|\+)[^/+]+(/|\+)[^/?]+)'

EXTERNAL_COURSE_KEY_PATTERN = r'([A-Za-z0-9-_:]+)'

COURSE_ID_PATTERN = r'(?P<course_id>({}|{}))'.format(
    INTERNAL_COURSE_KEY_PATTERN,
    EXTERNAL_COURSE_KEY_PATTERN
)

# Captures strings composed of alphanumeric characters, dashes, and underscores.
PROGRAM_KEY_PATTERN = r'(?P<program_key>[A-Za-z0-9-_]+)'

# Captures hex strings with dashes (a superset of UUIDs).
# We could match UUIDs more strictly, but we validate the UUIDs in
# jobs.get_job_status anyway, so it's not necessary.
JOB_ID_PATTERN = r'(?P<job_id>[0-9a-f-]+)'

ORGANIZATION_KEY_PATTERN = r'[A-Za-z0-9-_]+'

PROGRAM_CACHE_KEY_TPL = 'program:{uuid}'
