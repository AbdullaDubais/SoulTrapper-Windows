"""Small OpenAL Soft backend for mono positional sources and headphone HRTF."""
import ctypes as C
from array import array
from pathlib import Path
import wave
from sound_levels import enhance
from host import openal_library, prepare_library_path


class SpatialAudio:
    def __init__(self, root, loopback=False):
        self.device = self.context = None
        self.sources, self.buffers = {}, {}
        self.paused = []
        self.reverb = False
        self.effect = C.c_uint()
        self.slot = C.c_uint()
        library = openal_library(root)
        prepare_library_path(library)
        self.lib = C.CDLL(str(library))
        signatures = [
            ('alcOpenDevice',C.c_void_p,[C.c_char_p]),
            ('alcCloseDevice',C.c_int,[C.c_void_p]),
            ('alcCreateContext',C.c_void_p,[C.c_void_p,C.POINTER(C.c_int)]),
            ('alcMakeContextCurrent',C.c_int,[C.c_void_p]),
            ('alcDestroyContext',None,[C.c_void_p]),
            ('alcGetIntegerv',None,[C.c_void_p,C.c_int,C.c_int,C.POINTER(C.c_int)]),
            ('alGetString',C.c_char_p,[C.c_int]),
            ('alGetError',C.c_int,[]),
            ('alGenBuffers',None,[C.c_int,C.POINTER(C.c_uint)]),
            ('alDeleteBuffers',None,[C.c_int,C.POINTER(C.c_uint)]),
            ('alBufferData',None,[C.c_uint,C.c_int,C.c_void_p,C.c_int,C.c_int]),
            ('alGenSources',None,[C.c_int,C.POINTER(C.c_uint)]),
            ('alDeleteSources',None,[C.c_int,C.POINTER(C.c_uint)]),
            ('alSourcei',None,[C.c_uint,C.c_int,C.c_int]),
            ('alSourcef',None,[C.c_uint,C.c_int,C.c_float]),
            ('alSource3f',None,[C.c_uint,C.c_int,C.c_float,C.c_float,C.c_float]),
            ('alSource3i',None,[C.c_uint,C.c_int,C.c_int,C.c_int,C.c_int]),
            ('alGetProcAddress',C.c_void_p,[C.c_char_p]),
            ('alSourcePlay',None,[C.c_uint]),
            ('alSourceStop',None,[C.c_uint]),
            ('alSourcePause',None,[C.c_uint]),
            ('alGetSourcei',None,[C.c_uint,C.c_int,C.POINTER(C.c_int)]),
            ('alListener3f',None,[C.c_int,C.c_float,C.c_float,C.c_float]),
            ('alListenerfv',None,[C.c_int,C.POINTER(C.c_float)]),
            ('alDistanceModel',None,[C.c_int]),
        ]
        for name,result,args in signatures:
            fn=getattr(self.lib,name);fn.restype=result;fn.argtypes=args
        try:
            if loopback:
                fn=self.lib.alcLoopbackOpenDeviceSOFT
                fn.restype=C.c_void_p;fn.argtypes=[C.c_char_p]
                self.device=fn(None)
            else:self.device=self.lib.alcOpenDevice(None)
            if not self.device:raise OSError('No OpenAL playback device')
            # Request stereo headphone HRTF. Query the actual result, never assume it.
            values=[0x1992,1,0x1007,44100]
            if loopback:values += [0x1990,0x1501,0x1991,0x1402]
            attrs=(C.c_int*(len(values)+1))(*values,0)
            self.context=self.lib.alcCreateContext(self.device,attrs)
            if not self.context:raise OSError('OpenAL context failed')
            if not self.lib.alcMakeContextCurrent(self.context):raise OSError('OpenAL activation failed')
            enabled=C.c_int()
            self.lib.alcGetIntegerv(self.device,0x1992,1,C.byref(enabled))
            self.hrtf=bool(enabled.value)
            self.version=self.lib.alGetString(0xB002).decode()
            self.lib.alDistanceModel(0xD002)
            self.lib.alListenerfv(0x100F,(C.c_float*6)(0,0,-1,0,1,0))
            self.listener(0,0)
            
        except Exception:
            self.close()
            raise

    def listener(self,x,y):
        self.lib.alListener3f(0x1004,x,0,y)

    def play(self,key,name,position,gain=1,loop=False,relative=False,reference=1):
        if key not in self.sources:
            source=C.c_uint();self.lib.alGenSources(1,C.byref(source));self.sources[key]=source
        source=self.sources[key]
        self.lib.alSourceStop(source)
        self.lib.alSourcei(source,0x1009,self.buffers[name].value)
        self.lib.alSourcei(source,0x0202,int(relative))
        self.lib.alSourcei(source,0x1007,int(loop))
        self.lib.alSourcef(source,0x100A,gain)
        self.lib.alSourcef(source,0x1020,reference)
        self.lib.alSourcef(source,0x1021,1.)
        self.lib.alSource3f(source,0x1004,*position)
        self.lib.alSourcePlay(source)

    def move(self,key,position):
        if key in self.sources:self.lib.alSource3f(self.sources[key],0x1004,*position)

    def stop(self,key):
        if key in self.sources:self.lib.alSourceStop(self.sources[key])

    def pause(self):
        self.paused=[]
        for source in self.sources.values():
            state=C.c_int();self.lib.alGetSourcei(source,0x1010,C.byref(state))
            if state.value==0x1012:self.lib.alSourcePause(source);self.paused.append(source)

    def resume(self):
        for source in self.paused:self.lib.alSourcePlay(source)
        self.paused=[]

    def close(self):
        if self.context:
            for source in self.sources.values():
                self.lib.alSourceStop(source);self.lib.alDeleteSources(1,C.byref(source))
            for buffer in self.buffers.values():self.lib.alDeleteBuffers(1,C.byref(buffer))
            if self.slot.value:self.alDeleteAuxiliaryEffectSlots(1,C.byref(self.slot))
            if self.effect.value:self.alDeleteEffects(1,C.byref(self.effect))
            self.lib.alcMakeContextCurrent(None);self.lib.alcDestroyContext(self.context)
            self.context=None
        if self.device:self.lib.alcCloseDevice(self.device);self.device=None

    def load(self,name,path,attack=None):
        if name in self.buffers:return
        with wave.open(str(path)) as w:
            rate=w.getframerate();data=w.readframes(w.getnframes())
        begin=0
        if attack and Path(attack).exists():
            with wave.open(str(attack)) as w:prefix=w.readframes(w.getnframes())
            begin=len(prefix)//2;data=prefix+data
        data=enhance(name,data)
        buf=C.c_uint();self.lib.alGenBuffers(1,C.byref(buf));self.buffers[name]=buf
        self.lib.alBufferData(buf,0x1101,data,len(data),rate)
        if begin:
            fn=self.lib.alBufferiv;fn.argtypes=[C.c_uint,C.c_int,C.POINTER(C.c_int)]
            fn(buf,0x2015,(C.c_int*2)(begin,len(data)//2))
        if self.lib.alGetError():raise OSError('Could not load positional effect '+name)
    def busy(self,key):
        if key not in self.sources:return False
        state=C.c_int();self.lib.alGetSourcei(self.sources[key],0x1010,C.byref(state))
        return state.value in (0x1012,0x1013)
    def seek(self,key,seconds):
        if key in self.sources:self.lib.alSourcef(self.sources[key],0x1024,seconds)
    def offset(self,key):
        if key not in self.sources:return 0.
        fn=self.lib.alGetSourcef;fn.argtypes=[C.c_uint,C.c_int,C.POINTER(C.c_float)]
        n=C.c_float();fn(self.sources[key],0x1024,C.byref(n));return n.value
