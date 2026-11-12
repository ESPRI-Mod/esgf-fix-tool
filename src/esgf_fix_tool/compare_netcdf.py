
import os
import sys
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

# Configuration
MAX_ELEMENTS = 2_000_000
MAX_EXAMPLES = 5
TOLERANCE = 1e-12

args = [arg for arg in sys.argv[1:] if arg != "--no-color"]

if len(args) < 2:
    sys.exit(
        "Usage: python compare_netcdf.py "
        "original.nc corrected.nc [main_variable] [tolerance] [--no-color]"
    )

original, corrected = args[:2]
main_var = args[2] if len(args) > 2 else Path(original).name.split("_")[0]
tolerance = float(args[3]) if len(args) > 3 else TOLERANCE

# Enable ANSI colors by default, even when running through pcocc/esgf-toolbox.
USE_COLOR = "--no-color" not in sys.argv and "NO_COLOR" not in os.environ

COLORS = {
    "ADDED": "\033[32m",
    "REMOVED": "\033[31m",
    "MODIFIED": "\033[33m",
    "RENAMED": "\033[36m",
    "REPLACED": "\033[35m",
    "SKIPPED": "\033[90m",
}
RESET = "\033[0m"


def status(label):
    """Return a colored status label."""
    value = f"[{label}]"
    if not USE_COLOR:
        return value
    return f"{COLORS.get(label, '')}{value}{RESET}"


def fmt(value, maxlen=180):
    """Format values with full floating-point precision."""
    result = repr(value)
    return result[:maxlen] + "..." if len(result) > maxlen else result


def same(a, b):
    """Compare attribute values exactly, including NaNs."""
    a = np.asarray(a)
    b = np.asarray(b)
    try:
        return bool(np.array_equal(a, b, equal_nan=True))
    except (TypeError, ValueError):
        return bool(np.array_equal(a, b))


def attribute_changes(a, b):
    """Compare NetCDF attributes."""
    changes = []
    old_names = set(a.ncattrs())
    new_names = set(b.ncattrs())

    for name in sorted(old_names | new_names):
        if name not in old_names:
            changes.append(
                f"      {status('ADDED')} {name} = {fmt(b.getncattr(name))}"
            )
        elif name not in new_names:
            changes.append(
                f"      {status('REMOVED')} {name} = {fmt(a.getncattr(name))}"
            )
        else:
            old = a.getncattr(name)
            new = b.getncattr(name)

            if not same(old, new):
                changes.extend([
                    f"      {status('MODIFIED')} {name}",
                    f"          OLD: {fmt(old)}",
                    f"          NEW: {fmt(new)}",
                ])

    return changes


def detect_renames(ds1, ds2):
    """Identify renamed bounds through coordinate references."""
    renames = {}

    for name in set(ds1.variables) & set(ds2.variables):
        a = ds1.variables[name]
        b = ds2.variables[name]

        if "bounds" not in a.ncattrs() or "bounds" not in b.ncattrs():
            continue

        old = a.getncattr("bounds")
        new = b.getncattr("bounds")

        if (
            old != new
            and old in ds1.variables
            and new in ds2.variables
            and old not in ds2.variables
            and new not in ds1.variables
        ):
            renames[old] = new

    return renames


def compare_values(a, b):
    """Compare coordinate values exactly, including masks."""
    if a.shape != b.shape:
        return [
            (f"      {status('SKIPPED')} Values: "
             f"shape {a.shape} -> {b.shape}")
        ]

    if a.size > MAX_ELEMENTS:
        return [
            (f"      {status('SKIPPED')} Values: "
             f"{a.size:,} elements exceed limit")
        ]

    old = np.ma.asarray(a[...])
    new = np.ma.asarray(b[...])

    old_mask = np.ma.getmaskarray(old)
    new_mask = np.ma.getmaskarray(new)

    x = np.asarray(np.ma.getdata(old))
    y = np.asarray(np.ma.getdata(new))

    valid = ~old_mask & ~new_mask
    mask_changed = old_mask != new_mask

    numeric = (
        np.issubdtype(x.dtype, np.number)
        and np.issubdtype(y.dtype, np.number)
    )

    if numeric:
        # Preserve numeric precision for float64 coordinate comparisons.
        exact_equal = np.asarray(
            np.equal(x, y) | (np.isnan(x) & np.isnan(y))
        )
        close_equal = np.asarray(
            np.isclose(
                x, y, rtol=0, atol=tolerance, equal_nan=True
            )
        )
    else:
        exact_equal = np.asarray(np.equal(x, y))
        close_equal = exact_equal

    exact_diff = mask_changed | (valid & ~exact_equal)
    significant_diff = mask_changed | (valid & ~close_equal)

    count = int(np.count_nonzero(exact_diff))
    significant = int(np.count_nonzero(significant_diff))

    if count == 0:
        return []

    lines = [
        f"      {status('MODIFIED')} {count:,}/{a.size:,} values changed",
        (f"          Significant differences (>{tolerance:g}): "
         f"{significant:,}"),
    ]

    # Always display the first exact changes, even within tolerance.
    for idx in np.argwhere(exact_diff)[:MAX_EXAMPLES]:
        index = tuple(int(i) for i in idx)
        lines.append(
            f"          index {index}: "
            f"{fmt(old[index])} -> {fmt(new[index])}"
        )

    if count > MAX_EXAMPLES:
        lines.append(f"          ... and {count - MAX_EXAMPLES:,} more")

    return lines


def describe(var):
    """Describe a NetCDF variable without reading its data."""
    return (
        f"dtype={var.dtype}, "
        f"dimensions={var.dimensions}, "
        f"shape={var.shape}"
    )


with Dataset(original) as ds1, Dataset(corrected) as ds2:

    print("=" * 80)
    print("NETCDF COMPARISON")
    print("=" * 80)
    print(f"ORIGINAL : {original}")
    print(f"CORRECTED: {corrected}")
    print(f"MAIN VAR : {main_var} (data excluded)")
    print(f"TOLERANCE: {tolerance:g}")

    renames = detect_renames(ds1, ds2)

    # 1. Dimensions
    print("\n=== 1. DIMENSIONS ===")

    dim_changes = []

    for name in sorted(set(ds1.dimensions) | set(ds2.dimensions)):
        if name not in ds1.dimensions:
            dim_changes.append(
                f"  {status('ADDED')} {name}: {len(ds2.dimensions[name])}"
            )
        elif name not in ds2.dimensions:
            dim_changes.append(
                f"  {status('REMOVED')} {name}: {len(ds1.dimensions[name])}"
            )
        else:
            a = ds1.dimensions[name]
            b = ds2.dimensions[name]

            if len(a) != len(b) or a.isunlimited() != b.isunlimited():
                dim_changes.append(
                    f"  {status('MODIFIED')} {name}: "
                    f"{len(a)} -> {len(b)}"
                )

    print("\n".join(dim_changes) if dim_changes else "  No changes")

    # 2. Global attributes
    print("\n=== 2. GLOBAL ATTRIBUTES ===")

    changes = attribute_changes(ds1, ds2)
    print("\n".join(changes) if changes else "  No changes")

    # 3. Variables
    print("\n=== 3. VARIABLES ===")

    old_names = set(ds1.variables)
    new_names = set(ds2.variables)

    pairs = [
        (name, name, False)
        for name in sorted(old_names & new_names)
    ]

    pairs.extend(
        (old, new, True)
        for old, new in sorted(renames.items())
    )

    matched_old = {old for old, _, _ in pairs}
    matched_new = {new for _, new, _ in pairs}

    changed_variables = 0

    for old_name, new_name, renamed in pairs:
        a = ds1.variables[old_name]
        b = ds2.variables[new_name]

        attrs = attribute_changes(a, b)
        structure = []

        if a.dtype != b.dtype:
            structure.append(
                f"      {status('MODIFIED')} dtype: {a.dtype} -> {b.dtype}"
            )

        if a.dimensions != b.dimensions:
            structure.append(
                f"      {status('MODIFIED')} dimensions: "
                f"{a.dimensions} -> {b.dimensions}"
            )

        # Never read the main geophysical data matrix.
        values = (
            []
            if old_name == main_var or new_name == main_var
            else compare_values(a, b)
        )

        if not (renamed or attrs or structure or values):
            continue

        changed_variables += 1

        if renamed:
            print(
                f"\n  {status('RENAMED')} VARIABLE: "
                f"{old_name} -> {new_name}"
            )
        else:
            print(f"\n  {status('MODIFIED')} VARIABLE: {old_name}")

        if structure:
            print("    STRUCTURE:")
            print("\n".join(structure))

        if attrs:
            print("    ATTRIBUTES:")
            print("\n".join(attrs))

        if values:
            print("    VALUES:")
            print("\n".join(values))

    # Removed variables
    for name in sorted(old_names - matched_old):
        changed_variables += 1
        print(f"\n  {status('REMOVED')} VARIABLE: {name}")
        print(f"      {describe(ds1.variables[name])}")

    # Added variables
    for name in sorted(new_names - matched_new):
        var = ds2.variables[name]
        changed_variables += 1

        print(f"\n  {status('ADDED')} VARIABLE: {name}")
        print(f"      {describe(var)}")

        if var.ncattrs():
            print("    ATTRIBUTES:")
            for attr in sorted(var.ncattrs()):
                print(
                    f"      {status('ADDED')} {attr} = "
                    f"{fmt(var.getncattr(attr))}"
                )

    if changed_variables == 0:
        print("  No variable changes")

    # Summary
    print("\n=== SUMMARY ===")
    print(f"  Dimensions changed : {len(dim_changes)}")
    print(f"  Variables changed  : {changed_variables}")
    print(f"  Bounds renamed     : {len(renames)}")
    print(f"  Main data compared : NO ({main_var})")
    print("=" * 80)
