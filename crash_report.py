"""Local-only error reports. Reports are never uploaded automatically."""
import platform, sys, traceback
from datetime import datetime, timezone
from pathlib import Path
from host import in_mac_app_bundle, is_portable, user_data_dir

def write_error(context=''):
 module=Path(__file__).resolve().parent
 root=module.parent if module.name=='_internal' else module
 text=('Soul Trapper 1.1\n'+datetime.now(timezone.utc).isoformat()+'\n'
       +platform.platform()+'\nPython '+sys.version+'\n'+context+'\n'+traceback.format_exc())
 # Windows keeps its original order: beside the game first, per-user second,
 # first folder that will take the file wins. On Mac a log inside the bundle
 # is useless to the player, so it goes to the per-user folder instead -
 # unless the player asked for portable saves with portable.txt.
 folders=[user_data_dir()] if in_mac_app_bundle() and not is_portable(module) else [root,user_data_dir()]
 for folder in folders:
  try:
   folder.mkdir(parents=True,exist_ok=True)
   target=folder/'error.log'
   with target.open('a',encoding='utf8') as f:f.write('\n'+text+'\n')
   return target
  except OSError:continue
 return None
