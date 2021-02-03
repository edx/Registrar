#!/usr/bin/python3
"""
Script to generate fake JSON result files for program enrollment GET endpoint.

Requires Python>=3.6

Usage:
    python3 fake_program_enrollments.py <student_key_length> <count>
Example:
    python3 fake_program_enrollments.py 16 100
"""

import json
import random
import sys


STATUS_CHOICES = ['enrolled', 'pending', 'suspended', 'canceled']
STATUS_WEIGHTS = [0.5, 0.3, 0.1, 0.1]


def main(args):
    """
    Main function.

    Arguments:
        args (list[str]): Command-line args excluding script name.

    Returns: int
        Exit code
    """
    help_text = (
        'Usage: python fake_program_enrollments.py ' +
        '<student_key_length> <count>'
    )
    if len(args) != 2:
        print(help_text)
        return 1
    try:
        key_length = int(args[0])
        count = int(args[1])
    except ValueError:
        print(help_text)
        return 1
    enrolls = generate_fake_enrollments(key_length, count)
    print(json.dumps(enrolls, indent=4))
    return 0


def generate_fake_enrollments(key_length, count):
    """
    Generate fake program enrollment list.

    Arguments:
        key_length (int): number of digits for student keys
        count (int): number of enrollment dicts to generate

    Returns: list[dict]
    """
    enrollments = {}
    while len(enrollments) < count:
        key = generate_fake_student_key(key_length)
        if key in enrollments:
            continue  # Skip duplicate keys
        enrollments[key] = {
            'student_key': key,
            'status': random.choices(STATUS_CHOICES, weights=STATUS_WEIGHTS)[0],
            'account_exists': random.random() < 0.6,
        }
    return list(enrollments.values())


def generate_fake_student_key(key_length):
    """
    Generate a fake student key (a hex string)

    Args:
        key_length (int): number of hex digits for student key

    Returns: str
    """
    hex_digits = (
        [str(i) for i in range(10)] +
        [chr(i) for i in range(ord('a'), ord('g'))]
    )
    return ''.join(random.choice(hex_digits) for _ in range(key_length))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
