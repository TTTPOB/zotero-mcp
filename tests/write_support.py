"""Small shared fakes for write-version tests."""

import copy


def item_versions(item, *versions):
    """Answer with each local version in turn; repeat the final version."""
    remaining = list(versions)

    def read(item_key):
        version = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return {**copy.deepcopy(item), "key": item_key, "version": version}

    return read
