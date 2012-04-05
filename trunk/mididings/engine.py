# -*- coding: utf-8 -*-
#
# mididings
#
# Copyright (C) 2008-2012  Dominic Sacré  <dominic.sacre@gmx.de>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#

import _mididings

import mididings.patch as _patch
import mididings.scene as _scene
import mididings.util as _util
import mididings.misc as _misc
import mididings.setup as _setup
import mididings.overload as _overload
import mididings.arguments as _arguments
from mididings.setup import get_config as _get_config
from mididings.setup import get_hooks as _get_hooks
from mididings.units.base import _UNIT_TYPES

import time as _time
import weakref as _weakref
import threading as _threading
import gc as _gc
import atexit as _atexit
import os as _os
import sys as _sys

if _sys.version_info >= (3,):
    raw_input = input


_TheEngine = None


def _make_portnames(ports, prefix):
    if _misc.issequence(ports):
        return ports
    else:
        return [prefix + str(_util.offset(n)) for n in range(ports)]


class Engine(_mididings.Engine):
    def __init__(self, scenes, control, pre, post):
        # build the actual port names
        self.in_ports = _make_portnames(_get_config('in_ports'), 'in_')
        self.out_ports = _make_portnames(_get_config('out_ports'), 'out_')

        # initialize C++ base class
        engine_args = (
            _get_config('backend'),
            _get_config('client_name'),
            self.in_ports,
            self.out_ports,
            not _get_config('silent')
        )
        _mididings.Engine.__init__(self, *engine_args)

        self._scenes = {}

        for i, s in scenes.items():
            if isinstance(s, _scene.SceneGroup):
                self._scenes[i] = (s.name, [])

                for v in s.subscenes:
                    name, proc, init = self._parse_scene(v)
                    self.add_scene(_util.actual(i), _patch.Patch(proc), _patch.Patch(init))
                    self._scenes[i][1].append(name)
            else:
                name, proc, init = self._parse_scene(s)

                self._scenes[i] = (name, [])
                self.add_scene(_util.actual(i), _patch.Patch(proc), _patch.Patch(init))

        control_patch = _patch.Patch(control) if control else None
        pre_patch = _patch.Patch(pre) if pre else None
        post_patch = _patch.Patch(post) if post else None
        self.set_processing(control_patch, pre_patch, post_patch)

        self._quit = _threading.Event()

        # delay before actually sending any midi data (give qjackctl patchbay time to react...)
        delay = _get_config('start_delay')
        if delay != None:
            if delay > 0:
                _time.sleep(delay)
            else:
                raw_input("press enter to start midi processing...")

        global _TheEngine
        _TheEngine = _weakref.ref(self)

        _gc.collect()
        _gc.disable()

    def run(self):
        self._call_hooks('on_start')

        n = _get_config('initial_scene')
        if n in self._scenes:
            # scene number
            initial_scene = _util.actual(n)
            initial_subscene = -1
        elif _misc.issequence(n) and len(n) > 1 and n[0] in self._scenes:
            # scene number as tuple...
            initial_scene = _util.actual(n[0])
            if _util.actual(n[1]) < len(self._scenes[n[0]][1]):
                # ...and valid subscene
                initial_subscene = _util.actual(n[1])
            else:
                initial_subscene = -1
        else:
            initial_scene = -1
            initial_subscene = -1
        self.start(initial_scene, initial_subscene)

        try:
            # wait() with no timeout also blocks KeyboardInterrupt, but a very long timeout doesn't. weird...
            while not self._quit.isSet():
                self._quit.wait(86400)
        except KeyboardInterrupt:
            pass
        finally:
            self._call_hooks('on_exit')
            global _TheEngine
            _TheEngine = None

    def process_file(self):
        self.start(0)

    def _parse_scene(self, s):
        if isinstance(s, _scene.Scene):
            init = [s.init_patch] if s.init_patch else []
            proc = s.patch
            name = s.name if s.name else ''
        elif isinstance(s, tuple):
            init = [s[0]]
            proc = s[1]
            name = ''
        else:
            init = []
            proc = s
            name = ''
        init += _patch.get_init_patches(proc)
        return name, proc, init

    def scene_switch_callback(self, scene, subscene):
        # the scene and subscene parameters are the actual numbers without offset!
        if scene == -1:
            # no scene specified, use current
            scene = _mididings.Engine.current_scene(self)
        if subscene == -1:
            # no subscene specified, use first
            subscene = 0

        # save actual subscene index
        subscene_index = subscene

        # add data offset to scene/subscene numbers
        scene = _util.offset(scene)
        subscene = _util.offset(subscene)

        found = (scene in self._scenes and
                 (not subscene_index or subscene_index < len(self._scenes[scene][1])))

        # get string representation of scene/subscene number
        if subscene_index or (scene in self._scenes and len(self._scenes[scene][1])):
            number = "%d.%d" % (scene, subscene)
        else:
            number = str(scene)

        if not _get_config('silent'):
            if found:
                # get scene/subscene name
                scene_data = self._scenes[scene]
                if scene_data[1]:
                    name = "%s - %s" % (scene_data[0], scene_data[1][subscene_index])
                else:
                    name = scene_data[0]

                if name:
                    print("switching to scene %s: %s" % (number, name))
                else:
                    print("switching to scene %s" % number)
            else:
                print("no such scene: %s" % number)

        if found:
            self._call_hooks('on_switch_scene', scene, subscene)

    def _call_hooks(self, name, *args):
        for hook in _get_hooks():
            if hasattr(hook, name):
                f = getattr(hook, name)
                f(*args)

    def switch_scene(self, scene, subscene=None):
        _mididings.Engine.switch_scene(self,
            _util.actual(scene),
            _util.actual(subscene) if subscene != None else -1
         )

    def switch_subscene(self, subscene):
        _mididings.Engine.switch_scene(self, -1, _util.actual(subscene))

    def current_scene(self):
        return _util.offset(_mididings.Engine.current_scene(self))

    def current_subscene(self):
        return _util.offset(_mididings.Engine.current_subscene(self))

    def scenes(self):
        return self._scenes

    def output_event(self, ev):
        ev._finalize()
        _mididings.Engine.output_event(self, ev)

    def process(self, ev):
        ev._finalize()
        return _mididings.Engine.process(self, ev)

    def restart(self):
        _atexit.register(self._restart)
        self.quit()

    @staticmethod
    def _restart():
        # run the same interpreter with the same arguments again
        _os.execl(_sys.executable, _sys.executable, *_sys.argv)

    def quit(self):
        self._quit.set()



@_overload.mark
@_arguments.accept(_UNIT_TYPES)
def run(patch):
    """
    Create the engine and start event processing. This function does not
    usually return until mididings exits.
    """
    e = Engine({_util.offset(0): patch}, None, None, None)
    e.run()

@_overload.mark
@_arguments.accept(_UNIT_TYPES, _arguments.nullable(_UNIT_TYPES), _arguments.nullable(_UNIT_TYPES), _arguments.nullable(_UNIT_TYPES))
def run(scenes, control=None, pre=None, post=None):
    """
    Create the engine and start event processing. This function does not
    usually return until mididings exits.
    """
    e = Engine(scenes, control, pre, post)
    e.run()


def process_file(infile, outfile, patch):
    """
    Process a MIDI file using the smf backend.
    """
    _setup._config_impl(
        backend='smf',
    )
    _setup.config(
        in_ports=[infile],
        out_ports=[outfile],
    )
    e = Engine({0: patch}, None, None, None)
    e.process_file()


def switch_scene(scene, subscene=None):
    """
    Switch to the given scene number.
    """
    _TheEngine().switch_scene(scene, subscene)

def switch_subscene(subscene):
    """
    Switch to the given subscene number.
    """
    _TheEngine().switch_subscene(subscene)

def current_scene():
    """
    Return the current scene number.
    """
    return _TheEngine().current_scene()

def current_subscene():
    """
    Return the current subscene number.
    """
    return _TheEngine().current_subscene()

def scenes():
    """
    Return a mapping from scene numbers to a tuple of the form
    (scene_name, [subscene_name, ...]).
    """
    return _TheEngine().scenes()

def output_event(ev):
    """
    Send an event directly to an output port.
    """
    _TheEngine().output_event(ev)

def in_ports():
    """
    Return the list of input port names.
    """
    if active():
        return _TheEngine().in_ports
    else:
        return _make_portnames(_get_config('in_ports'), 'in_')

def out_ports():
    """
    Return the list of output port names.
    """
    if active():
        return _TheEngine().out_ports
    else:
        return _make_portnames(_get_config('out_ports'), 'out_')

def time():
    """
    Return a floating point number of seconds since some unspecified starting
    point.
    """
    return _TheEngine().time()

def active():
    """
    Return True if the engine is running, otherwise False.
    """
    return _TheEngine != None and _TheEngine() != None

def restart():
    """
    Restart mididings.
    """
    _TheEngine().restart()

def quit():
    """
    Quit mididings.
    """
    _TheEngine().quit()
