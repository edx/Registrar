"""
This utility would invoke pyyaml to load and dump the DRY yaml file
with both merge key anchors and shared dictionary elements.
Pyyaml would handle the merge key elements properly with overrides.
However, Pyyaml would not expand anchored shared dictionary elements.
We then use the ruamel commandline tool "merge-expand" to make sure shared
dictionary elements are expanded.
"""
import os
import sys

import yaml


def main():
    """
    Main script logic
    """
    # First use pyyaml to do merge key expand
    with open(sys.argv[1]) as fp:
        data = yaml.safe_load(fp)

    output_path = sys.argv[2]
    if output_path == '-':
        output_path = '/tmp/generated.yml'

    with open(output_path, 'w') as wp:
        yaml.dump(data, wp)

    # Now, use ruamel yaml to merge-expand
    os.system('yaml merge-expand {} {}'.format(output_path, sys.argv[2]))

    if output_path != sys.argv[2]:
        os.remove(output_path)


if __name__ == "__main__":
    main()
