{
  "patcher": {
    "fileversion": 1,
    "appversion": {
      "major": 8,
      "minor": 5,
      "revision": 0,
      "architecture": "x64",
      "modernui": 1
    },
    "classnamespace": "box",
    "rect": [60, 100, 920, 620],
    "bglocked": 0,
    "openinpresentation": 0,
    "default_fontsize": 12.0,
    "default_fontface": 0,
    "default_fontname": "Arial",
    "gridonopen": 1,
    "gridsize": [15.0, 15.0],
    "gridsnaponopen": 1,
    "objectsnaponopen": 1,
    "statusbarvisible": 2,
    "toolbarvisible": 1,
    "lefttoolbarpinned": 0,
    "toptoolbarpinned": 0,
    "righttoolbarpinned": 0,
    "bottomtoolbarpinned": 0,
    "toolbars_unpinned_last_save": 0,
    "tallnewobj": 0,
    "boxanimatetime": 200,
    "enablehscroll": 1,
    "enablevscroll": 1,
    "devicewidth": 0.0,
    "description": "",
    "digest": "",
    "tags": "",
    "style": "",
    "subpatcher_template": "",
    "assistshowspatchername": 0,
    "boxes": [
      {
        "box": {
          "id": "obj-pin",
          "maxclass": "newobj",
          "text": "plugin~",
          "numinlets": 0,
          "numoutlets": 2,
          "outlettype": ["signal", "signal"],
          "patching_rect": [60, 60, 50, 22]
        }
      },
      {
        "box": {
          "id": "obj-pout",
          "maxclass": "newobj",
          "text": "plugout~",
          "numinlets": 2,
          "numoutlets": 0,
          "patching_rect": [60, 540, 56, 22]
        }
      },
      {
        "box": {
          "id": "obj-thisdev",
          "maxclass": "newobj",
          "text": "live.thisdevice",
          "numinlets": 1,
          "numoutlets": 3,
          "outlettype": ["bang", "bang", ""],
          "patching_rect": [340, 60, 100, 22]
        }
      },
      {
        "box": {
          "id": "obj-openmsg",
          "maxclass": "message",
          "text": "open /tmp/tone_forge_render_poc/current.wav",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [""],
          "patching_rect": [340, 110, 290, 22]
        }
      },
      {
        "box": {
          "id": "obj-livepath",
          "maxclass": "newobj",
          "text": "live.path live_set",
          "numinlets": 1,
          "numoutlets": 3,
          "outlettype": ["", "", ""],
          "patching_rect": [340, 170, 115, 22]
        }
      },
      {
        "box": {
          "id": "obj-observer",
          "maxclass": "newobj",
          "text": "live.observer is_playing",
          "numinlets": 2,
          "numoutlets": 3,
          "outlettype": ["", "", ""],
          "patching_rect": [340, 210, 150, 22]
        }
      },
      {
        "box": {
          "id": "obj-sel",
          "maxclass": "newobj",
          "text": "sel 1 0",
          "numinlets": 1,
          "numoutlets": 3,
          "outlettype": ["bang", "bang", ""],
          "patching_rect": [340, 290, 60, 22]
        }
      },
      {
        "box": {
          "id": "obj-trig",
          "maxclass": "newobj",
          "text": "t b b",
          "numinlets": 1,
          "numoutlets": 2,
          "outlettype": ["bang", "bang"],
          "patching_rect": [340, 315, 50, 22]
        }
      },
      {
        "box": {
          "id": "obj-msg1",
          "maxclass": "message",
          "text": "1",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [""],
          "patching_rect": [340, 340, 30, 22]
        }
      },
      {
        "box": {
          "id": "obj-msg0",
          "maxclass": "message",
          "text": "0",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [""],
          "patching_rect": [400, 340, 30, 22]
        }
      },
      {
        "box": {
          "id": "obj-sfrec",
          "maxclass": "newobj",
          "text": "sfrecord~ 2",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": ["int"],
          "patching_rect": [340, 430, 200, 22]
        }
      },
      {
        "box": {
          "id": "obj-print",
          "maxclass": "newobj",
          "text": "print TF_RENDER",
          "numinlets": 1,
          "numoutlets": 0,
          "patching_rect": [560, 250, 115, 22]
        }
      },
      {
        "box": {
          "id": "obj-note",
          "maxclass": "comment",
          "text": "tone_forge M4L recorder PoC. Records master signal to /tmp/tone_forge_render_poc/poc_render.wav while Live transport is playing.",
          "numinlets": 1,
          "numoutlets": 0,
          "patching_rect": [60, 20, 750, 22]
        }
      }
    ],
    "lines": [
      {"patchline": {"source": ["obj-pin", 0], "destination": ["obj-pout", 0]}},
      {"patchline": {"source": ["obj-pin", 1], "destination": ["obj-pout", 1]}},
      {"patchline": {"source": ["obj-pin", 0], "destination": ["obj-sfrec", 0]}},
      {"patchline": {"source": ["obj-pin", 1], "destination": ["obj-sfrec", 1]}},
      {"patchline": {"source": ["obj-thisdev", 0], "destination": ["obj-openmsg", 0]}},
      {"patchline": {"source": ["obj-thisdev", 0], "destination": ["obj-livepath", 0]}},
      {"patchline": {"source": ["obj-openmsg", 0], "destination": ["obj-sfrec", 0]}},
      {"patchline": {"source": ["obj-livepath", 0], "destination": ["obj-observer", 0]}},
      {"patchline": {"source": ["obj-observer", 0], "destination": ["obj-sel", 0]}},
      {"patchline": {"source": ["obj-observer", 0], "destination": ["obj-print", 0]}},
      {"patchline": {"source": ["obj-sel", 0], "destination": ["obj-trig", 0]}},
      {"patchline": {"source": ["obj-trig", 1], "destination": ["obj-openmsg", 0]}},
      {"patchline": {"source": ["obj-trig", 0], "destination": ["obj-msg1", 0]}},
      {"patchline": {"source": ["obj-sel", 1], "destination": ["obj-msg0", 0]}},
      {"patchline": {"source": ["obj-msg1", 0], "destination": ["obj-sfrec", 0]}},
      {"patchline": {"source": ["obj-msg0", 0], "destination": ["obj-sfrec", 0]}}
    ]
  }
}
