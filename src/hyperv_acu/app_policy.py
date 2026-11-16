import os
from hyperv_acu.bootstrap import load_settings
from hyperv_acu import vm_pool

VM_ID = str(os.environ.get('HYPERV_VM_ID') or load_settings().get('vm_id', '')).strip().lower()
CAPTURE_MAX_AGE = 30

def require_vm(vm_id):
    if not isinstance(vm_id, str) or not vm_id.strip():
        raise ValueError('Select a managed Hyper-V VM by its exact ID')
    key = vm_id.strip().lower()
    if VM_ID and key == VM_ID:
        return
    if key in vm_pool.list_managed_ids():
        return
    if not VM_ID and not vm_pool.list_managed_ids():
        raise ValueError('Configure vm_id in config/settings.local.json or HYPERV_VM_ID')
    raise ValueError('Select the configured Hyper-V VM by its exact ID')

def require_capture(capture,target,now):
    if (not isinstance(capture,dict) or capture.get('target')!=target or
        not 0<=now-capture.get('time',0)<=CAPTURE_MAX_AGE):
        raise ValueError('A fresh screenshot of this exact target is required')

def pixel_point(x,y,width,height):
    if any(type(v) is not int for v in (x,y,width,height)) or not (0<=x<width and 0<=y<height):
        raise ValueError('Click coordinates must be within the fresh screenshot')
    return x,y

def normalized_point(x,y,width,height):
    if any(type(v) is not int for v in (x,y,width,height)) or not (0<=x<=1000 and 0<=y<=1000 and width>0 and height>0):
        raise ValueError('Normalized coordinates must be between 0 and 1000')
    return round(x*(width-1)/1000),round(y*(height-1)/1000)

def require_background_click(class_name):
    if class_name in ('UnityWndClass','Launcher'):
        raise ValueError('Background clicks are unavailable for this game or launcher; no input was sent')
