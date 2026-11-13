"""Optional structured progress, independent of terminal output and NetCDF operations."""
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType

TITLES = {
    'drop_variable_histories': 'Suppression de history sur toutes les variables, auxiliaires compris',
    'reorder_climatology_cell_methods': 'Ordre de cell_methods prescrit par la DR',
    'drop_basin_latitude_bounds': 'Bornes latitude conventionnelles (ancien plan)',
    'clean_retired_metadata': 'Suppression des variables et dimensions techniques inutilisées',
    'harmonize_main_description': 'Description commune des fichiers ta/zg à six heures',
    'rebuild_monthly_climatology': 'Reconstruction des douze moyennes climatologiques mensuelles',
    'normalize_hybrid_coordinate': 'Normalisation de lev et de ses bornes avec P_REF=101325 Pa',
    'fix_ice_passage_sector': 'Labels des quatre passages de glace',
    'convert_ice_radius': 'Conversion des rayons effectRadIc en microns',
    'add_reviewed_coordinate_bounds': 'Bornes physiques approuvées du sol ou de plev7c',
    'fix_orchidee_soil_depth': 'Profondeurs et bornes du sol ORCHIDEE',
    'fix_basin_sector': 'Labels sector des trois bassins océaniques',
    'fix_msfty_coordinates': 'Coordonnées rlat et lev de msfty',
    'reduce_replicated_zostoga': 'Réduction vérifiée de zostoga en série temporelle',
    'convert_basin_region_mask': 'Conversion du masque basin en entier et ajout des flags',
    'sync_filename_grid_label': 'Synchronisation du nom de fichier et de grid_label',
    'fix_ocean_vertical_metadata': 'Identité CF de la profondeur océanique',
    'rename_redundant_text_label': 'Suppression du libellé textuel redondant après vérification de sector',
    'normalize_text_padding': 'Normalisation du remplissage des chaînes',
    'fix_registry_comment': 'Retrait des échappements Markdown du commentaire',
    'drop_unused_horizontal_bounds': 'Retrait des bornes de coordonnée inutilisée',
    'drop_var_attr': "Suppression d'un attribut de variable",
    'rename_var_attr': "Renommage d'un attribut de variable",
    'set_var_attr': "Correction d'un attribut de variable",
    'set_var_attr_if_matches': "Correction conditionnelle d'un attribut",
    'drop_descriptive_standard_name': 'Retrait du standard_name descriptif',
    'drop_region_units': 'Retrait des unités de la région',
    'fix_soil_depth_metadata': 'Métadonnées de profondeur du sol',
    'fix_invalid_standard_names': 'Correction des standard_name invalides',
    'rename_coordinate': 'Renommage de coordonnée',
    'rename_auxiliary_dimension': 'Renommage de dimension auxiliaire',
    'add_registry_scalar': 'Ajout de coordonnée scalaire DR',
    'add_text_coordinate_alias': 'Alias de coordonnée textuelle',
    'fix_data_layout': 'Ordre des dimensions et singletons',
    'fix_scalar_label': 'Correction de valeur catégorielle',
    'normalize_regular_longitude': 'Arrondi de la longitude et de ses bornes',
    'detach_instantaneous_time_bounds': 'Suppression des bornes instantanées dégénérées',
    'add_registry_bounds': 'Bornes fournies par la DR',
    'fix_dr_histogram': 'Classes et bornes de l’histogramme DR',
    'add_temperature_units_metadata': 'Qualification CF des unités de température',
    'describe_numeric_basin': 'Métadonnées des codes de bassin',
    'drop_olevel_bounds_units': 'Suppression des unités dupliquées sur les bornes océaniques',
    'set_reviewed_time_long_name': 'Libellé du temps selon la DR',
    'set_reviewed_coordinate_long_name': 'Libellé de la coordonnée selon la DR',
    'rename_scalar_strlen': 'Renommage de la dimension des chaînes',
    'rename_ocean_vertices_dimension': 'Renommage de la dimension des sommets océaniques',
    'set_reduced_grid_label': 'Identifiant de grille de la sortie réduite',
    'fix_iceband': 'Classes et bornes d’épaisseur de la glace',
    'fix_climatology_reference': 'Référence aux bornes climatologiques',
    'rename_bounds_variable': 'Renommage des bornes',
    'normalize_bounds_dimension': 'Normalisation de la dimension bnds',
    'drop_unrequested_horizontal_reference': 'Retrait de référence horizontale superflue',
    'rename_horizontal_coordinate': 'Renommage de coordonnée horizontale',
    'drop_osurf_axis': 'Suppression de l’attribut axis de seasurface',
    'integrate_heat_transport': 'Somme verticale des transports hfx/hfy',
    'center_hybrid_interfaces': 'Moyenne verticale edt/evu : 80 interfaces vers 79 centres',
    'add_basin_latitude_bounds': 'Bornes conventionnelles lat/rlat des neuf profils NEMO',
    'fix_hybrid_coordinate_metadata': 'Noms et attributs hybrides uniquement',
    'fix_hybrid_coefficients': 'Métadonnées hybrides et coefficients de référence',
    'restore_hybrid_vertical_coefficients': 'Ancienne reconstruction hybride désactivée',
    'fix_basin_profile_latitude': 'Profil lat(lat) et latitude terminale approuvée',
}


@dataclass(frozen=True)
class ProgressEvent:
    kind: str
    status: str
    path: str = ''
    phase: str = ''
    rule: str = ''
    fix: str = ''
    title: str = ''
    params: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    duration: float = 0.


_callback = ContextVar('fix_progress_callback', default=None)
# Read-only default: every scope sets a new dict, none is mutated in place.
_scope = ContextVar('fix_progress_scope', default=MappingProxyType({}))


@contextmanager
def reporting(callback, path):
    token = _callback.set(callback)
    scope = _scope.set({'path': str(path)})
    try:
        yield
    finally:
        _scope.reset(scope)
        _callback.reset(token)


@contextmanager
def step_scope(step):
    token = _scope.set(dict(_scope.get(), rule=step.rule, fix=step.fix, params=step.params,
                            title=TITLES.get(step.fix, step.fix.replace('_', ' '))))
    try:
        yield
    finally:
        _scope.reset(token)


def notify(kind, status, *, duration=0., **details):
    callback = _callback.get()
    if callback is not None:
        callback(ProgressEvent(kind, status, **_scope.get(), details=details, duration=duration))


@contextmanager
def activity(phase):
    """Report blocking work as well as its errors; never print a premature Done for a fix."""
    token = _scope.set(dict(_scope.get(), phase=phase))
    start = time.monotonic()
    notify('phase', 'start')
    try:
        yield
    except BaseException as exc:
        status = 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'failed'
        notify('phase', status, duration=time.monotonic()-start, error=str(exc) or type(exc).__name__)
        raise
    else:
        notify('phase', 'done', duration=time.monotonic()-start)
    finally:
        _scope.reset(token)


def io_progress(operation, variable, completed, total, itemsize):
    notify('io', 'progress', operation=operation, variable=variable,
           completed=int(completed), total=int(total), itemsize=int(itemsize))
