"""Versioned application files and user-owned data have separate roots."""
from pathlib import Path
PROGRAM=Path(__file__).resolve().parents[1]
PACKAGE_ROOT=PROGRAM.parent
MODELS=PACKAGE_ROOT/'models'
PROJECTS=PACKAGE_ROOT/'projects'
CACHE=PACKAGE_ROOT/'cache'
LOGS=PACKAGE_ROOT/'logs'
USER_RESOURCES=PACKAGE_ROOT/'user_data'
MAINTENANCE=PACKAGE_ROOT/'maintenance'
MOVED={'projects':PROJECTS,'cache':CACHE,'logs':LOGS,'tests':MAINTENANCE/'tests',
       'validation':MAINTENANCE/'validation','docs':MAINTENANCE/'docs','backups':MAINTENANCE/'backups'}
USER_FOLDERS={'voice_library','portrait_templates','asset_sets'}

def legacy_path(value,program=PROGRAM):
    """Resolve old program-relative paths without changing historical JSON."""
    program=Path(program).resolve();package=program.parent
    path=Path(value)
    if path.is_absolute():
        try:rel=path.resolve().relative_to(program)
        except ValueError:return path.resolve()
    else:rel=path
    parts=rel.parts
    if parts and parts[0] in MOVED:
        destination=package/(('maintenance/'+parts[0]) if parts[0] in ('tests','validation','docs','backups') else parts[0])
        return (destination/Path(*parts[1:])).resolve()
    if len(parts)>1 and parts[0]=='resources' and parts[1] in USER_FOLDERS:
        return (package/'user_data'/Path(*parts[1:])).resolve()
    return (program/rel).resolve()

def initialize():
    for p in (PROJECTS,CACHE,LOGS,USER_RESOURCES):p.mkdir(parents=True,exist_ok=True)

def project_logs(project):
    # Logs live outside projects, so replacing application files cannot erase them.
    import hashlib
    project=Path(project).resolve()
    try:key=project.relative_to(PROJECTS.resolve()).as_posix()
    except ValueError:key=project.name+'-'+hashlib.sha256(str(project).encode()).hexdigest()[:10]
    folder=LOGS/'projects'/key;folder.mkdir(parents=True,exist_ok=True);return folder
