#!/usr/bin/python3
"""
Script to generate fake JSON result files for course enrollment GET endpoint.

The input to the script is the desired enrollment count and the file path of
a JSON program enrollment list. The output is a JSON list of a subset
input program enrollments.

Requires Python>=3.6

Usage:
    python3 fake_course_enrollments.py <program_enrollment_input_file> <count>
Example:
    python3 fake_course_enrollments.py pgm-enrolls.json 25
"""

import json
import random
import sys


# Map from program enrollment statuses to sets of possible course
# enrollment statuses.
STATUS_MAP = {
    'enrolled':
        (['active', 'inactive'], [0.75, 0.25]),
    'pending':
        (['active', 'inactive'], [0.75, 0.25]),
    'suspended':
        (['inactive'], [1]),
    'canceled':
        (['inactive'], [1]),
}


def main(args):
    """
    Main function.

    Arguments:
        args (list[str]): Command-line args excluding script name.

    Returns: int
        Exit code
    """
    help_text = (
        'Usage: python fake_course_enrollments.py ' +
        '<program_enrollment_input_file> <count>'
    )
    if len(args) != 2:
        print(help_text)
        return 1
    try:
        with open(args[0]) as f:
            text = f.read()
        program_enrollments = json.loads(text)
    except OSError:
        print('Count not read input file', args[0])
        return 1
    try:
        count = int(args[1])
    except ValueError:
        print(help_text)
        return 1
    enrolls = generate_fake_enrollments(program_enrollments, count)
    print(json.dumps(enrolls, indent=4))
    return 0


def generate_fake_enrollments(program_enrollments, count):
    """
    Generate fake course enrollment list.

    Arguments:
        program_enrollments (list[dict]):
            pool of program enrollments to draw from
        count (int): number of enrollment dicts to generate

    Returns: list[dict]
    """
    course_enrollments = random.sample(program_enrollments, count)
    for enrollment in course_enrollments:
        choices, weights = STATUS_MAP[enrollment['status']]
        enrollment['status'] = random.choices(choices, weights=weights)[0]
    return course_enrollments


sys.exit(main(sys.argv[1:]))
