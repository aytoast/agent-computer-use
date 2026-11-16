"""Crash-safe single-action input state. Unknown outcomes stay blocked."""
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

SAFE_NO_INPUT={'user_active','paused','protected_desktop','stale_frame','invalid_target',
               'display_changed','invalid_point','target_obscured','no_task_context',
               'control_interface_unavailable','isolation_unverified','vm_foreground'}

def _read(path):
    path=Path(path)
    if not path.exists():return None
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):raise ValueError('Input state is unreadable; manual review is required') from None

def _write(path,state):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(state,f)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def require_clear(path):
    if _read(path) is not None:raise ValueError('Previous guest input needs review before another action')

def start(path,target):
    if not isinstance(target,str) or not target:raise ValueError('Exact target is required')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise ValueError('Previous guest input needs review before another action') from None
    with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump({'state':'pending','target':target},f)

def finish(path,result):
    state=_read(path)
    if not isinstance(state,dict) or state.get('state')!='pending':raise ValueError('Input state is not pending')
    outcome=result.get('outcome') if isinstance(result,dict) else None
    if (outcome in SAFE_NO_INPUT or
        (isinstance(result,dict) and outcome=='error' and
         result.get('message')=='overlay_motion_timeout' and result.get('mouse_down_sent') is False)):
        Path(path).unlink();return
    state['state']='awaiting_observation' if outcome=='delivered' else 'uncertain'
    state['outcome']=outcome
    _write(path,state)

def observed(path,target):
    state=_read(path)
    if (isinstance(state,dict) and state.get('state')=='observation_available'
        and state.get('target')==target and isinstance(state.get('review_token'),str)
        and state['review_token']):
        return state['review_token']
    if isinstance(state,dict) and state.get('state')=='awaiting_observation' and state.get('target')==target:
        state['state']='observation_available'
        state['review_token']=uuid.uuid4().hex
        _write(path,state)
        return state['review_token']

def confirm(path,target,review_token,evidence):
    state=_read(path)
    if (not isinstance(state,dict) or state.get('state')!='observation_available'
        or state.get('target')!=target or state.get('review_token')!=review_token):
        raise ValueError('No matching delivered action and subsequent screenshot')
    if not isinstance(evidence,str) or len(evidence.strip())<12:
        raise ValueError('Describe the observed result before continuing')
    audit=Path(path).with_name('hyperv-input-review.jsonl')
    with audit.open('a',encoding='utf-8') as f:
        f.write(json.dumps({'target':target,'result':evidence.strip(),'reviewed_at':datetime.now(timezone.utc).isoformat()})+'\n')
    Path(path).unlink()
