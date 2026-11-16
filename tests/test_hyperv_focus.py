import vm_universal as module

class Backend:
    def __init__(self):
        self.current={'paused':False,'desktop':'Default','held':False,'idle_ms':9000,'tick':1}
        self.valid=True
        self.calls=0
    def state(self):return self.current.copy()
    def validate_target(self):return self.valid
    def activate(self):self.calls+=1;return True

def test_focus_requires_idle_user_and_valid_target():
    backend=Backend()
    assert module.focus_window(backend)['outcome']=='focused'
    backend.current['held']=True
    assert module.focus_window(backend)['outcome']=='user_active'
    assert backend.calls==1
    backend.current['held']=False
    backend.valid=False
    assert module.focus_window(backend)['outcome']=='invalid_target'
    assert backend.calls==1

def test_focus_stops_on_protected_desktop():
    backend=Backend()
    backend.current['desktop']='Winlogon'
    assert module.focus_window(backend)['outcome']=='protected_desktop'
    assert backend.calls==0
