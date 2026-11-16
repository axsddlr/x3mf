"""Cross-slicer setting tables: aliases, value grammars and transfer rules.

The slicers in the Orca lineage mostly agree on setting *names* and disagree on
some *values*: a support style is ``organic`` in one and ``tree_organic`` in
another, vertical shell thickness is expressed in three different grammars, and
a handful of settings are the same thing under two names. This module holds
those tables plus the gate that decides whether a converted value is one the
destination will actually accept.

Two things this module decides, and our projection cannot:

* **Aliases** — a setting that exists under a different name in the destination
  (``enable_support_ironing`` vs ``support_ironing``), including the ones that
  fan out to several keys (``wall_filament`` → outer/inner wall ids).
* **Validity** — a source value is written only when the destination would
  actually accept it: right type, member of the destination's choice list, slot
  index within range, and not one of the values a given destination refuses.
  Anything else falls back to the destination's own value and is reported,
  instead of shipping a project the slicer will silently reinterpret.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Typed families
# ---------------------------------------------------------------------------

POSITIVE = frozenset({"layer_height", "initial_layer_print_height"})

NON_NEGATIVE = frozenset({
    "brim_width", "brim_object_gap", "support_top_z_distance", "support_bottom_z_distance",
    "support_object_xy_distance", "top_shell_thickness", "bottom_shell_thickness",
    "support_angle", "support_base_pattern_spacing", "support_interface_spacing",
    "support_bottom_interface_spacing", "support_object_first_layer_gap", "support_flow_ratio",
    "support_interface_flow_ratio", "support_ironing_spacing", "support_ironing_direction",
    "support_ironing_inset", "raft_contact_distance", "raft_expansion",
    "tree_support_branch_angle", "tree_support_branch_angle_organic",
    "tree_support_branch_diameter", "tree_support_branch_diameter_angle",
    "tree_support_branch_diameter_organic", "tree_support_branch_distance",
    "tree_support_branch_distance_organic", "tree_support_tip_diameter",
    "tree_support_angle_slow", "tree_support_brim_width",
    "bottom_solid_infill_flow_ratio", "top_solid_infill_flow_ratio", "print_flow_ratio",
    "bridge_flow", "internal_bridge_flow", "elefant_foot_compensation", "max_bridge_length",
    "ironing_spacing", "fuzzy_skin_point_distance", "fuzzy_skin_thickness",
    "minimum_sparse_infill_area", "skirt_distance", "slice_closing_radius", "resolution",
    "brim_ears_detection_length", "brim_ears_max_angle", "wall_transition_angle",
    "make_overhang_printable_angle",
})

INTEGERS = frozenset({
    "wall_loops", "top_shell_layers", "bottom_shell_layers", "raft_layers",
    "support_interface_top_layers", "support_threshold_angle", "enforce_support_layers",
    "support_interface_bottom_layers", "tree_support_wall_count",
    "elefant_foot_compensation_layers", "skirt_loops", "skirt_height",
    "wall_distribution_count", "slow_down_layers", "seam_slope_steps",
})

#: Integers that also accept -1 (meaning "unset" / "auto").
MINUS_ONE_ALLOWED = frozenset({"support_interface_bottom_layers", "tree_support_wall_count"})

SWITCHES = frozenset({
    "enable_support", "support_on_build_plate_only", "only_one_wall_top",
    "only_one_wall_first_layer", "spiral_mode", "alternate_extra_wall",
    "independent_support_layer_height", "support_critical_regions_only",
    "support_interface_loop_pattern", "support_interface_not_for_body",
    "support_remove_small_overhang", "support_ironing", "enable_support_ironing",
    "tree_support_adaptive_layer_height", "tree_support_auto_brim", "bridge_no_support",
    "accel_to_decel_enable", "enable_arc_fitting", "enable_overhang_speed",
    "detect_overhang_wall", "detect_thin_wall", "detect_narrow_internal_solid_infill",
    "infill_combination", "extra_perimeters_on_overhangs", "infill_first",
    "is_infill_first", "reduce_crossing_wall", "interface_shells", "precise_outer_wall",
    "precise_z_height", "rotate_solid_infill_direction", "seam_slope_conditional",
    "seam_slope_entire_loop", "seam_slope_inner_walls", "staggered_inner_seams",
    "thick_bridges", "thick_internal_bridges", "make_overhang_printable",
})

CHOICES: dict[str, tuple[str, ...]] = {
    "sparse_infill_pattern": (
        "grid", "gyroid", "rectilinear", "line", "honeycomb", "3dhoneycomb", "cubic",
        "adaptivecubic", "supportcubic", "triangles", "trihexagon", "concentric",
    ),
    "top_surface_pattern": ("rectilinear", "monotonic", "monotonicline", "concentric", "hilbertcurve"),
    "bottom_surface_pattern": ("rectilinear", "monotonic", "monotonicline", "concentric", "hilbertcurve"),
    "internal_solid_infill_pattern": ("rectilinear", "zig-zag", "monotonic", "monotonicline", "concentric"),
    "support_type": ("normal(auto)", "normal(manual)", "tree(auto)", "tree(manual)"),
    "support_style": ("default", "grid", "snug", "tree_slim", "tree_strong", "tree_hybrid"),
    "support_base_pattern": ("default", "rectilinear", "rectilinear-grid", "honeycomb", "lightning", "hollow"),
    "support_interface_pattern": ("auto", "rectilinear", "concentric", "rectilinear_interlaced", "grid"),
    "support_ironing_pattern": ("concentric", "rectilinear", "zig-zag"),
    "wall_generator": ("classic", "arachne"),
    "ensure_vertical_shell_thickness": (
        "0", "1", "none", "ensure_critical_only", "ensure_moderate", "ensure_all",
        "disabled", "partial", "enabled",
    ),
    "seam_position": ("nearest", "aligned", "back", "random"),
    "brim_type": ("auto_brim", "brim_ears", "outer_only", "inner_only", "outer_and_inner", "no_brim"),
    "ironing_type": ("no ironing", "top", "topmost", "solid"),
    "ironing_pattern": ("rectilinear", "zig-zag", "concentric"),
    "print_sequence": ("by layer", "by object"),
    "wall_sequence": ("inner wall/outer wall", "outer wall/inner wall", "inner-outer-inner wall"),
    "slicing_mode": ("regular", "even_odd", "close_holes"),
    "draft_shield": ("disabled", "limited", "enabled"),
    "fuzzy_skin": ("none", "external", "all"),
}

#: `organic` is accepted by the Orca family and `tree_organic` by Bambu; the
#: table above carries Bambu's spelling, so both are allowed explicitly.
SUPPORT_STYLE_ORGANIC = frozenset({"organic", "tree_organic"})

SLOT_KEYS = frozenset({
    "support_filament", "support_interface_filament", "solid_infill_filament",
    "sparse_infill_filament", "wall_filament", "outer_wall_filament_id",
    "inner_wall_filament_id", "internal_solid_filament_id", "sparse_infill_filament_id",
    "top_surface_filament_id", "bottom_surface_filament_id",
})

PERCENTAGES = frozenset({
    "sparse_infill_density", "skeleton_infill_density", "skin_infill_density",
    "support_threshold_overlap", "support_ironing_flow", "raft_first_layer_density",
    "tree_support_top_rate", "ironing_flow", "seam_gap", "bridge_density",
    "internal_bridge_density", "accel_to_decel_factor", "infill_wall_overlap",
    "top_bottom_infill_wall_overlap",
})

SIGNED = frozenset({
    "support_expansion", "raft_first_layer_expansion", "xy_contour_compensation",
    "xy_hole_compensation", "bridge_angle", "internal_bridge_angle", "infill_direction",
    "solid_infill_direction", "ironing_angle",
})

NUMERIC_OR_PERCENT = frozenset({
    "support_line_width", "line_width", "initial_layer_line_width", "inner_wall_line_width",
    "outer_wall_line_width", "sparse_infill_line_width", "internal_solid_infill_line_width",
    "top_surface_line_width", "infill_anchor", "infill_anchor_max",
    "small_perimeter_threshold", "min_bead_width", "initial_layer_min_bead_width",
    "min_feature_size", "wall_transition_filter_deviation", "wall_transition_length",
    "bridge_speed", "internal_bridge_speed", "gap_infill_speed", "initial_layer_speed",
    "initial_layer_infill_speed", "initial_layer_travel_speed", "inner_wall_speed",
    "outer_wall_speed", "internal_solid_infill_speed", "sparse_infill_speed",
    "top_surface_speed", "support_speed", "support_interface_speed", "ironing_speed",
    "skirt_speed", "small_perimeter_speed", "travel_speed", "travel_speed_z",
    "overhang_1_4_speed", "overhang_2_4_speed", "overhang_3_4_speed", "overhang_4_4_speed",
    "default_acceleration", "bridge_acceleration", "initial_layer_acceleration",
    "inner_wall_acceleration", "outer_wall_acceleration", "internal_solid_infill_acceleration",
    "sparse_infill_acceleration", "top_surface_acceleration", "travel_acceleration",
    "default_jerk", "infill_jerk", "initial_layer_jerk", "inner_wall_jerk", "outer_wall_jerk",
    "top_surface_jerk", "travel_jerk",
})

#: Keys that may hold one value per print extruder.  A source array whose
#: entries differ cannot be carried as a scalar.
VARIANT_KEYS = frozenset({
    "initial_layer_speed", "initial_layer_infill_speed", "outer_wall_speed",
    "inner_wall_speed", "small_perimeter_speed", "small_perimeter_threshold",
    "sparse_infill_speed", "internal_solid_infill_speed", "top_surface_speed",
    "enable_overhang_speed", "overhang_1_4_speed", "overhang_2_4_speed",
    "overhang_3_4_speed", "overhang_4_4_speed", "bridge_speed", "gap_infill_speed",
    "support_speed", "support_interface_speed", "travel_speed", "travel_speed_z",
    "default_acceleration", "travel_acceleration", "initial_layer_acceleration",
    "outer_wall_acceleration", "inner_wall_acceleration", "sparse_infill_acceleration",
    "top_surface_acceleration", "top_solid_infill_flow_ratio",
})

# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

ALIASES: dict[str, tuple[str, ...]] = {
    "enable_support_ironing": ("support_ironing",),
    "support_ironing": ("enable_support_ironing",),
    "infill_first": ("is_infill_first",),
    "is_infill_first": ("infill_first",),
    "wall_filament": ("outer_wall_filament_id", "inner_wall_filament_id"),
    "solid_infill_filament": (
        "internal_solid_filament_id", "top_surface_filament_id", "bottom_surface_filament_id",
    ),
    "sparse_infill_filament": ("sparse_infill_filament_id",),
}


def alias_targets(key: str, target_keys) -> list[str]:
    """Destination names for ``key`` that the destination actually declares."""
    if key in target_keys:
        return []
    return [name for name in ALIASES.get(key, ()) if name in target_keys]


# ---------------------------------------------------------------------------
# Destination profiles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Destination:
    """How the target spells things, derived from its flavor and its own values."""

    key: str = "orca"

    @property
    def creality(self) -> bool:
        return self.key == "creality"

    @property
    def organic_support(self) -> bool:
        """Orca-lineage slicers spell the organic support style ``organic``."""
        return self.key != "bambu"


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

def _number(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _non_negative_number_or_percent(value: str) -> bool:
    raw = value[:-1] if value.endswith("%") else value
    number = _number(raw)
    return number is not None and number >= 0


def scalar_of(value) -> str | None:
    """A single string for a scalar, or for an array whose entries agree."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and value:
        first = value[0] if isinstance(value[0], str) else None
        if first is not None and all(item == first for item in value):
            return first
    return None


def can_transfer(
    key: str,
    value: str,
    target_value,
    slots: int,
    destination: Destination,
) -> bool:
    """Would the destination accept this value for this key?"""
    if key == "ensure_vertical_shell_thickness":
        family = shell_family(scalar_of(target_value) or "")
        if family == "orca":
            return value in {"none", "ensure_critical_only", "ensure_moderate", "ensure_all"}
        if family == "bambu":
            return value in {"disabled", "partial", "enabled"}
        if family == "boolean":
            return value in {"0", "1"}

    choices = CHOICES.get(key)
    if choices is not None:
        if key == "support_style":
            return value in SUPPORT_STYLE_ORGANIC or value in choices
        return value in choices

    if key in SWITCHES:
        return value in {"0", "1"}
    if key in INTEGERS:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return False
        return number >= 0 or (number == -1 and key in MINUS_ONE_ALLOWED)
    if key in SLOT_KEYS:
        try:
            return 0 < int(value) <= slots
        except (TypeError, ValueError):
            return False
    if key in POSITIVE:
        number = _number(value)
        return number is not None and number > 0
    if key in NON_NEGATIVE:
        number = _number(value)
        return number is not None and number >= 0
    if key in SIGNED:
        return _number(value) is not None
    if key in PERCENTAGES:
        if not value.endswith("%"):
            return False
        number = _number(value[:-1])
        return number is not None and 0 <= number <= 100
    if key in NUMERIC_OR_PERCENT:
        return _non_negative_number_or_percent(value)
    return True


def refuses(key: str, value: str, destination: Destination) -> str | None:
    """Values a specific destination rejects outright; returns the reason."""
    if not destination.creality:
        return None
    if key == "raft_first_layer_expansion":
        number = _number(value)
        if number is not None and number < 0:
            return "Creality Print refuses a negative raft_first_layer_expansion"
    if key == "tree_support_wall_count":
        number = _number(value)
        if number is not None and not 0 <= number <= 2:
            return "Creality Print keeps tree_support_wall_count between 0 and 2"
    if key in ("internal_solid_infill_pattern", "ironing_pattern", "support_ironing_pattern"):
        if value == "rectilinear":
            return f"Creality Print refuses rectilinear for {key}"
    return None


#: ``ensure_vertical_shell_thickness`` speaks three grammars.
SHELL_BOOLEAN = frozenset({"0", "1"})
SHELL_ORCA = frozenset({"none", "ensure_critical_only", "ensure_moderate", "ensure_all"})
SHELL_BAMBU = frozenset({"disabled", "partial", "enabled"})

_SHELL_SEMANTICS = {
    "0": "none", "none": "none", "disabled": "none",
    "1": "all", "ensure_all": "all", "enabled": "all",
    "ensure_critical_only": "critical",
    "ensure_moderate": "moderate", "partial": "moderate",
}

_SHELL_RENDER = {
    "boolean": {"none": "0", "critical": "0", "moderate": "1", "all": "1"},
    "orca": {
        "none": "none", "critical": "ensure_critical_only",
        "moderate": "ensure_moderate", "all": "ensure_all",
    },
    "bambu": {"none": "disabled", "critical": "disabled", "moderate": "partial", "all": "enabled"},
}


def shell_family(value: str) -> str | None:
    if value in SHELL_BOOLEAN:
        return "boolean"
    if value in SHELL_BAMBU:
        return "bambu"
    if value in SHELL_ORCA:
        return "orca"
    return None


def _translate_rule(key: str, value: str, target_value: str, destination: Destination) -> str:
    """The destination's spelling of ``value`` (identity when no rule applies)."""
    if key == "support_style":
        if value == "tree_organic" and destination.organic_support:
            return "organic"
        if value == "organic" and not destination.organic_support:
            return "tree_organic"
        return value
    if key == "support_type":
        return {
            "normal": "normal(manual)",
            "tree": "tree(manual)",
            "hybrid(auto)": "tree(auto)",
        }.get(value, value)
    if key == "support_base_pattern" and value == "none":
        return "hollow"
    if key == "support_ironing_pattern" and value == "zig-zag" and destination.key != "bambu":
        return "rectilinear"
    if key in ("internal_solid_infill_pattern", "ironing_pattern") and value == "zig-zag" and not destination.creality:
        return value
    if key == "ensure_vertical_shell_thickness":
        family = shell_family(target_value)
        if family is None:
            return value
        if value in _SHELL_RENDER[family].values():
            return value
        semantic = _SHELL_SEMANTICS.get(value)
        if semantic is None:
            return value
        return _SHELL_RENDER[family][semantic]
    return value


def translate(
    key: str,
    value,
    target_value,
    slots: int,
    destination: Destination,
) -> tuple[str | None, str | None]:
    """Return ``(value_for_destination, reason_if_refused)``.

    ``None`` as the value means "cannot carry this safely": the caller keeps the
    destination's own setting and reports why.
    """
    scalar = scalar_of(value)
    if scalar is None:
        if isinstance(value, (list, tuple)) and value:
            return None, f"{key} differs per extruder in the source and cannot be reduced to one value"
        return None, f"{key} is not a scalar"

    refusal = refuses(key, scalar, destination)
    if refusal:
        return None, refusal

    mapped = _translate_rule(key, scalar, scalar_of(target_value) or "", destination)
    if not can_transfer(key, mapped, target_value, slots, destination):
        return None, f"{key}={mapped!r} is not a value this destination accepts"
    return mapped, None
