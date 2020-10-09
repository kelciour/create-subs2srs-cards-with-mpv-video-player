# -*- coding: utf-8 -*-

"""
Anki Add-on: mpv2anki

Add new option ("Open Video...") in the Tools menu to open video with MPV (https://mpv.io) 
and create subs2srs-like cards.

Default Fields:
   Id            | Back_to_the_Future_(1985)_00.28.16.762 | Back_to_the_Future_(1985)_00.28.15.512-00.28.24.727
   Source        | Back to the Future (1985)
   Time          | 00:28:16.762
   Subtitle      | I, Dr. Emmett Brown, am about to embark on an historic journey.
   Image         | <img src="Back_to_the_Future_(1985)_00.28.16.762.jpg" />
   Audio         | [sound:Back_to_the_Future_(1985)_00.28.15.512-00.28.24.727.mp3]
   Video         | [sound:Back_to_the_Future_(1985)_00.28.15.512-00.28.24.727.webm]
   Video (HTML5) | Back_to_the_Future_(1985)_00.28.15.512-00.28.24.727.webm

The "{{Video (HTML5)}}" can be used to embed a video clip into an Anki card. It works with Anki 2.1 and on AnkiMobile or AnkiDroid.

External SRT subtitles are optional but they're required to populate the "{{Audio}}", "{{Video}}" and "{{Video (HTML5)}}" fields.

If some of the fields aren't necessary, they can be removed from the note type.

Usage Notes:
    - Open a video file via "Open Video..." option (Ctrl+O) in the Tools menu.
    - Press "b" to create an Anki card.
    
Nickolay <kelciour@gmail.com>
"""

__version__ = '1.0.0-alpha3'

import json
import glob
import os
import re
import subprocess
import sys

# import the main window object (mw) from aqt
from aqt import mw
# import the "get file" tool from utils.py
from aqt.utils import getFile, showWarning, isMac
# import all of the Qt GUI library
from aqt.qt import *

from anki.lang import _, langs

from anki.hooks import addHook
from aqt.studydeck import StudyDeck
from distutils.spawn import find_executable
from anki.utils import isWin
from anki.mpv import *

from . import icons_rc

try:
    from aqt.sound import _packagedCmd
except:
    from anki.sound import _packagedCmd

if isMac:
    # https://docs.brew.sh/FAQ#my-mac-apps-dont-find-usrlocalbin-utilities
    os.environ['PATH'] = "/usr/local/bin:" + os.environ['PATH']

ffmpeg_executable = find_executable("ffmpeg")

langs = [(lang, lc) for lang, lc in langs if not lang.startswith("English")]
langs = sorted(langs + [("English", "en")])

def getTimeParts(seconds):
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    millisecs = int(seconds * 1000) % 1000
    return (hours, mins, secs, millisecs)

def secondsToTimestamp(seconds):
    return '%02d:%02d:%02d.%03d' % getTimeParts(seconds)

def secondsToFilename(seconds):
    return secondsToTimestamp(seconds).replace(":", ".")

def getVideoFile():
    key = (_("Video") +
           " (*.avi *.mkv *.mp4 *.mov *.mpg *.mpeg *.webm)")
    return getFile(None, _("Open Video"), None, key, key="video")

def srt_time_to_seconds(time):
    split_time = time.split(',')
    major, minor = (split_time[0].split(':'), split_time[1])
    return int(major[0]) * 3600 + int(major[1]) * 60 + int(major[2]) + float(minor) / 1000

def seconds_to_srt_time(time):
    return '%02d:%02d:%02d,%03d' % getTimeParts(time)

srt_encodings = ["utf-8", "cp1251"]

def fix_glob_square_brackets(glob_pattern):
    # replace the left square bracket with [[]
    glob_pattern = re.sub(r'\[', '[[]', glob_pattern)
    # replace the right square bracket with []] but be careful not to replace
    # the right square brackets in the left square bracket's 'escape' sequence.
    glob_pattern = re.sub(r'(?<!\[)\]', '[]]', glob_pattern)

    return glob_pattern

def format_filename(filename):
    return filename.strip().replace('[', '').replace(']', '').replace(' ', '_')

class SubtitlesHelper():
    def __init__(self, filePath, configManager):
        self.filePath = filePath
        self.settings = configManager.getSettings()
        self.status_code = "success"
        self.init()

    def init(self):
        self.subs = []
        self.translations = []

        if not self.settings["subs_target_language_code"] == "":
            subs_base_path = os.path.splitext(self.filePath)[0]
            subs_filepattern = subs_base_path + "*" + self.settings["subs_target_language_code"] + "*" + ".srt"
            subs_filepattern = fix_glob_square_brackets(subs_filepattern)
            subs_list = glob.glob(subs_filepattern)
            if len(subs_list) > 0:
                self.subsPath = subs_list[0]
                self.subs = self.read_subtitles(self.subsPath)
            elif os.path.isfile(subs_base_path + ".srt"):
                self.subsPath = subs_base_path + ".srt"
                self.subs = self.read_subtitles(self.subsPath)

        if not self.settings["subs_native_language_code"] == "":
            subs_filepattern = subs_base_path + "*" + self.settings["subs_native_language_code"] + "*" + ".srt"
            subs_filepattern = fix_glob_square_brackets(subs_filepattern)
            subs_list = glob.glob(subs_filepattern)
            if len(subs_list) > 0:
                self.translationsPath = subs_list[0]
                self.translations = self.read_subtitles(self.translationsPath)

        if len(self.subs) != 0 and self.settings['subs_target_language_code'] == 'en':
            self.convert_into_sentences()

        if len(self.translations) != 0:
            self.sync_subtitles()

    def convert_to_unicode(self, file_content):
        for enc in srt_encodings:
            try:
                content = file_content.decode(enc)
                return (True, content)
            except UnicodeDecodeError:
                pass
        return (False, file_content)
        
    def read_subtitles(self, subsPath):
        content = open(subsPath, 'rb').read()
        if content[:3]==b'\xef\xbb\xbf': # with bom
            content = content[3:]

        ret_code, content = self.convert_to_unicode(content)
        if ret_code == False:
            showWarning("Can't decode subtitles. Please convert subtitles to UTF-8 encoding.")
            pass

        subs = []
        content = re.sub(r'\r\n', '\n', content)
        content = re.sub(r'\n\s*\n+', '\n\n', content)
        content = re.sub(r'(^\d+\n\d+:\d+:\d+,\d+\s+-->\s+\d+:\d+:\d+,\d+\s*\n)', r'#~~~~~~~~~~~~~~#\1', content, flags=re.M)
        for sub in content.strip().split('#~~~~~~~~~~~~~~#'):
            if not sub.strip():
                continue
            try:
                sub_chunks = sub.split('\n')
                if (len(sub_chunks) >= 3):
                    sub_timecode =  sub_chunks[1].split(' --> ')
                    sub_start = srt_time_to_seconds(sub_timecode[0].strip())
                    sub_end = srt_time_to_seconds(sub_timecode[1].strip())
                    sub_content = "\n".join(sub_chunks[2:]).replace("\t", " ")
                    sub_content = re.sub(r"<[^>]+>", "", sub_content)
                    sub_content = re.sub(r"^-", r"- ", sub_content)
                    sub_content = re.sub(r"(\W)-([^\W])", r"\1 - \2", sub_content, flags=re.UNICODE)
                    sub_content = re.sub(r"  +", " ", sub_content)
                    sub_content = sub_content.replace("\n", " ").strip()

                    if len(sub_content) > 0:
                        subs.append((sub_start, sub_end, sub_content))
            except:
                showWarning("An error occurred while parsing the subtitle file:\n'%s'.\n\nIncorrect subtitle:\n%s" % (os.path.basename(subsPath), sub), parent=mw)
                self.status_code = "error"
                return []

        return subs

    def remove_tags(self, sub):
        sub = re.sub(r"<[^>]+>", "", sub)
        sub = re.sub(r"  +", " ", sub)
        sub = sub.strip()

        return sub

    def convert_into_sentences(self):
        subs = []

        for sub in self.subs:
            sub_start = sub[0]
            sub_end = sub[1]
            sub_content = sub[2]

            if len(subs) > 0: 
                prev_sub_start = subs[-1][0]
                prev_sub_end = subs[-1][1]
                prev_sub_content = subs[-1][2]

                if (sub_start - prev_sub_end) <= 2 and (sub_end - prev_sub_start) < 15 and \
                    sub_content[0] not in ['"', "'", "(", "[", "-", u"“", u'♪'] and \
                    (prev_sub_content[-1] not in ['.', "!", "?", ")", ']', u'”'] or \
                    (prev_sub_content[-3:] == "..." and (sub_content[:3] == "..." or sub_content[0].islower() or re.match(r"^I\b", sub_content)))):

                    subs[-1] = [prev_sub_start, sub_end, prev_sub_content + " " + sub_content]
                else:
                    subs.append([sub_start, sub_end, sub_content])
            else:
                subs.append([sub_start, sub_end, sub_content])

        self.subs = subs

    def sync_subtitles(self):
        en_subs = self.subs
        ru_subs = self.translations

        subs = [ ([], [], []) for i in range(len(en_subs))]
        for ru_sub in ru_subs:
            ru_sub_start = ru_sub[0]
            ru_sub_end = ru_sub[1]

            for idx, en_sub in enumerate(en_subs):
                en_sub_start = en_sub[0]
                en_sub_end = en_sub[1]

                if en_sub_start < ru_sub_end and en_sub_end > ru_sub_start:
                    sub_start = en_sub_start if en_sub_start > ru_sub_start else ru_sub_start
                    sub_end = en_sub_end if ru_sub_end > en_sub_end else ru_sub_end

                    if (sub_end - sub_start) / (ru_sub_end - ru_sub_start) > 0.25:
                        subs[idx][0].append(ru_sub[0])
                        subs[idx][1].append(ru_sub[1])
                        subs[idx][2].append(ru_sub[2])
                        break

        self.translations = []
        for idx, sub in enumerate(subs):
            if len(sub[2]) == 0:
                self.translations.append((self.subs[idx][0], self.subs[idx][1], ""))
            else:
                self.translations.append((sub[0][0], sub[1][-1], " ".join(sub[2])))

        idx = 0
        while idx < len(self.subs) and len(self.subs) > 1:
            if self.translations[idx][2] == "":
                en_sub_start = self.subs[idx][0]
                en_sub_end = self.subs[idx][1]

                ru_prev_sub_start = 0
                ru_prev_sub_end = 0
                ru_next_sub_start = 0
                ru_next_sub_end = 0

                if idx > 0:
                    ru_prev_sub_start = self.translations[idx-1][0]
                    ru_prev_sub_end = self.translations[idx-1][1]

                if idx < len(self.subs) - 1:
                    ru_next_sub_start = self.translations[idx+1][0]
                    ru_next_sub_end = self.translations[idx+1][1]

                if idx == len(self.subs) - 1:
                    self.subs[idx-1] = [self.subs[idx-1][0], self.subs[idx][1], self.subs[idx-1][2] + " " + self.subs[idx][2]]
                elif en_sub_end <= ru_next_sub_start and idx > 0:
                    self.subs[idx-1] = [self.subs[idx-1][0], self.subs[idx][1], self.subs[idx-1][2] + " " + self.subs[idx][2]]
                elif en_sub_start >= ru_next_sub_start or en_sub_start >= ru_prev_sub_end:
                    self.subs[idx+1] = [self.subs[idx][0], self.subs[idx+1][1], self.subs[idx][2] + " " + self.subs[idx+1][2]]
                elif (ru_prev_sub_end - en_sub_start) > (en_sub_end - ru_next_sub_start) and idx > 0:
                    self.subs[idx-1] = [self.subs[idx-1][0], self.subs[idx][1], self.subs[idx-1][2] + " " + self.subs[idx][2]]
                else:
                    self.subs[idx+1] = [self.subs[idx][0], self.subs[idx+1][1], self.subs[idx][2] + " " + self.subs[idx+1][2]]

                del self.subs[idx]
                del self.translations[idx]
            else:
                idx += 1

    def filter_subtitles(self, clip_start, clip_end, pad_start, pad_end):
        subs_filtered = []

        for idx in range(len(self.subs)):
            sub_start, sub_end, sub_content = self.subs[idx]

            if sub_end > (clip_start + pad_start) and sub_start < (clip_end - pad_end):
                subs_filtered.append((sub_start - clip_start, sub_end - clip_start, sub_content))

            if sub_start > clip_end:
                break
        
        return subs_filtered

    def write_subtitles(self, clip_start, clip_end, pad_start, pad_end, filename):
        subs = self.filter_subtitles(clip_start, clip_end, pad_start, pad_end)

        f = open(filename, 'w', encoding='utf-8')
        for idx in range(len(subs)):
            f.write(str(idx+1) + "\n")
            f.write(seconds_to_srt_time(subs[idx][0]) + " --> " + seconds_to_srt_time(subs[idx][1]) + "\n")
            f.write(subs[idx][2] + "\n")
            f.write("\n")
        f.close()

    def get_subtitle_id(self, time_pos):
        for sub_id in range(len(self.subs)):
            sub_start, sub_end, sub_content = self.subs[sub_id]
            if sub_start <= time_pos and time_pos <= sub_end:
                return sub_id
    
    def get_subtitle(self, sub_id, translation=False):
        if sub_id < 0 or sub_id > len(self.subs) - 1 or (translation is True and len(self.translations) == 0):
            return (None, None, "")
        if not translation:
            return self.subs[sub_id]
        else:
            return self.translations[sub_id]

    def get_prev_subtitle(self, sub_id, translation=False):
        if sub_id <= 0 or (translation is True and len(self.translations) == 0):
            return (self.subs[0][0], self.subs[0][1], "")
        sub_start, sub_end, sub_text = self.subs[sub_id]
        prev_sub_start, prev_sub_end, prev_sub_text = self.subs[sub_id - 1]
        if sub_start - prev_sub_end > 5:
            return (sub_start, sub_end, "")
        elif not translation:
            return self.subs[sub_id - 1]
        else:
            return self.translations[sub_id - 1]
    
    def get_next_subtitle(self, sub_id, translation=False):
        if sub_id >= len(self.subs)-1 or (translation is True and len(self.translations) == 0):
            return (self.subs[-1][0], self.subs[-1][1], "")
        sub_start, sub_end, sub_text = self.subs[sub_id]
        next_sub_start, next_sub_end, next_sub_text = self.subs[sub_id + 1]
        if next_sub_start - sub_end > 5:
            return (sub_start, sub_end, "")
        elif not translation:
            return self.subs[sub_id + 1]
        else:
            return self.translations[sub_id + 1]

    def get_phrase(self, sub_id, translation=False):
        phrase_start, phrase_end, phrase_text = self.get_subtitle(sub_id, translation)

        if not translation:
            subs = self.subs
        else:
            subs = self.translations

        for sub in reversed(subs[:sub_id]):
            sub_start, sub_end, sub_text = sub
            if phrase_start - sub_end <= self.settings["gap_between_phrases"]:
                phrase_start = sub_start
                phrase_text = sub_text + " " + phrase_text
            else:
                break
               
        for sub in subs[sub_id+1:]:
            sub_start, sub_end, sub_text = sub
            if sub_start - phrase_end <= self.settings["gap_between_phrases"]:
                phrase_end = sub_end
                phrase_text += " " + sub_text
            else:
                break

        return (phrase_start, phrase_end, phrase_text)

class ConfigManager():
    def __init__(self):
        self.configPath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_files", "config.json")
        self.init()
        self.load()

    def init(self):
        self.default = {
            "default_model" : "mpv2anki",
            "default_deck" : "Default",
            "image_width" : -2,
            "image_height" : 260,
            "video_width" : -2,
            "video_height" : 260,
            "pad_start" : 250,
            "pad_end" : 250,
            "create_clips_with_phrases" : False,
            "phrase_default_model" : "mpv2anki",
            "phrase_default_deck" : "Default",
            "phrase_video_width" : -2,
            "phrase_video_height" : 260,
            "phrase_pad_start" : 750,
            "phrase_pad_end" : 750,
            "use_mpv" : False,
            "audio_ext" : "mp3",
            "subs_target_language": "English",
            "subs_target_language_code": "en",
            "subs_native_language": "",
            "subs_native_language_code": "",
            "gap_between_phrases" : 1.25
        }
        self.config = { key: value for key, value in self.default.items() }

    def load(self):
        if os.path.isfile(self.configPath):
            with open(self.configPath) as f:
                data = json.load(f)
            for key, value in data.items():
                self.config[key] = value

    def save(self):
        with open(self.configPath, 'w') as f:  
            json.dump(self.config, f)

    def getSettings(self):
        return self.config

    def getFields(self):
        return [
            "<ignored>",
            "Id",
            "Source",
            "Path",
            "Time",
            "Image",
            "Image (with subtitles)",
            "Line",
            "Line: before",
            "Line: after",
            "Meaning: line",
            "Meaning: line before",
            "Meaning: line after",
            "Audio",
            "Audio (with context)",
            "Video",
            "Video (with context)",
            "Video (HTML5)",
            "Video (HTML5 with context)",
            "Video Subtitles",
            "[webm] Video",
            "[webm] Video (with context)",
            "[webm] Video (HTML5)",
            "[webm] Video (HTML5 with context)"
        ]

    def updateMapping(self, model, data):
        if "mapping" not in self.config:
            self.config["mapping"] = {}
        self.config["mapping"][model] = data

    def getFieldsMapping(self, model):
        if "mapping" not in self.config or model not in self.config["mapping"]:
            return {}
        return self.config["mapping"][model]

# Fix for ... cannot be converted to PyQt5.QtCore.QObject in this context
class MessageHandler(QObject):
    create_anki_card = pyqtSignal(float, float, float, str)

class MPVMonitor(MPV):

    def __init__(self, executable, popenEnv, filePath, mpvConf, msgHandler):
        self.executable = executable
        self.popenEnv = popenEnv

        try:
            no_config_idx = self.default_argv.index("--no-config")
            self.default_argv.remove("--no-config")
        except ValueError:
            no_config_idx = -1

        super().__init__(window_id=None, debug=False)

        if no_config_idx >= 0:
            self.default_argv.insert(no_config_idx, "--no-config")

        self.filePath = filePath
        self.mpvConf = mpvConf
        self.msgHandler = msgHandler
        self.audio_id = "auto"
        self.audio_ffmpeg_id = 0
        self.sub_id = "auto"

        self.set_property("include", self.mpvConf)

        self.command("load-script", os.path.join(os.path.dirname(os.path.abspath(__file__)), "mpv2anki.lua"))

        self.command("loadfile", self.filePath, "append-play")
        
    def on_property_term_status_msg(self, statusMsg=None):
        m = re.match(r"^\[mpv2anki\] ([^#]+) # ([^#]+) # ([^#]+) # (.*)$", statusMsg, re.DOTALL)
        if m:
            timePos, timeStart, timeEnd, subText = m.groups()
            self.msgHandler.create_anki_card.emit(float(timePos), float(timeStart), float(timeEnd), subText)

    def on_property_aid(self, audio_id=None):
        self.audio_id = audio_id
        if audio_id == False:
            self.audio_ffmpeg_id = 0
        elif audio_id == "auto":
            track_count = int(self.get_property("track-list/count"))
            for i in range(1, track_count + 1):
                track_type = self.get_property("track-list/%d/type" % i)
                track_index = int(self.get_property("track-list/%d/ff-index" % i))
                track_selected = self.get_property("track-list/%d/selected" % i)

                if track_type == "audio" and track_selected == "yes":
                    self.audio_ffmpeg_id = track_index
                    break
        else:
            self.audio_ffmpeg_id = self.audio_id - 1

    def on_property_sid(self, sub_id=None):
        self.sub_id = sub_id if sub_id != False else "no"

    def on_end_file(self):
        try:
            self.close()
        except Exception:
            # Ignore pywintypes.error: (232, 'WriteFile', 'The pipe is being closed.') 
            pass

class AnkiHelper(QObject):

    def __init__(self, executable, popenEnv, filePath, configManager, subsManager):
        QObject.__init__(self, mw)
        self.filePath = filePath
        self.configManager = configManager
        self.subsManager = subsManager
        self.msgHandler = MessageHandler()
        self.mpvConf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mpv.conf")
        self.mpvManager = MPVMonitor(executable, popenEnv, filePath, self.mpvConf, self.msgHandler)
        self.mpvExecutable = executable
        self.settings = self.configManager.getSettings()
        self.popenEnv = popenEnv

        self.initFieldsMapping()

        self.msgHandler.create_anki_card.connect(self.createAnkiCard)

        addHook("unloadProfile", self.mpvManager.close)

    def initFieldsMapping(self):
        self.fieldsMap = {}

        fieldsMapDefault = {} 
        for k, v in self.configManager.getFieldsMapping(self.settings["default_model"]).items():
            if v not in fieldsMapDefault:
                fieldsMapDefault[v] = []
            fieldsMapDefault[v].append(k)
        self.fieldsMap["default_model"] = fieldsMapDefault

        fieldsMapPhrase = {}
        for k, v in self.configManager.getFieldsMapping(self.settings["phrase_default_model"]).items():
            if v not in fieldsMapPhrase:
                fieldsMapPhrase[v] = []
            fieldsMapPhrase[v].append(k)
        self.fieldsMap["phrase_default_model"] = fieldsMapPhrase

    def createAnkiCard(self, timePos, timeStart, timeEnd, subText):
        self.addNewCard(timePos, timeStart, timeEnd, subText)

        if self.settings["create_clips_with_phrases"]:
            self.addNewCard(timePos, timeStart, timeEnd, subText, key="phrase_", phraseMode=True)

    def subprocess_image(self, source, timePos, subprocess_calls, sub="no", suffix=""):
        image = "%s_%s%s.jpg" % (format_filename(source), secondsToFilename(timePos), suffix)
        imagePath = os.path.join(mw.col.media.dir(), image)
        if not self.settings["use_mpv"] and ffmpeg_executable and sub == "no":
            argv = ["ffmpeg"]
            argv += ["-ss", secondsToTimestamp(timePos)]
            argv += ["-i", self.filePath]
            argv += ["-vframes", "1"]
            argv += [imagePath]
        else:
            argv = [self.mpvExecutable, self.filePath]
            argv += ["--include=%s" % self.mpvConf]
            argv += ["--start=%s" % secondsToTimestamp(timePos)]
            argv += ["--audio=no"]
            argv += ["--sub=%s" % sub]
            argv += ["--sub-visibility=yes"]
            argv += ["--frames=1"]
            argv += ["--vf-add=lavfi-scale=%s:%s" % (self.settings["image_width"], self.settings["image_height"])]
            argv += ["--ovc=mjpeg"]
            argv += ["--o=%s" % imagePath]
        subprocess_calls.append(argv)
        return image

    def subprocess_audio(self, source, sub_start, sub_end, aid, aid_ff, subprocess_calls):
        audio = "%s_%s-%s.%s" % (format_filename(source), secondsToFilename(sub_start), secondsToFilename(sub_end), self.settings["audio_ext"])
        audioPath = os.path.join(mw.col.media.dir(), audio)
        if not self.settings["use_mpv"] and ffmpeg_executable:
            argv = ["ffmpeg"]
            argv += ["-ss", secondsToTimestamp(sub_start)]
            argv += ["-i", self.filePath]
            argv += ["-t", secondsToTimestamp(sub_end - sub_start)]
            argv += ["-map", "0:a:%d" % aid_ff]
            argv += ["-af", "afade=t=in:st={:.3f}:d={:.3f},afade=t=out:st={:.3f}:d={:.3f}".format(0, 0.25, sub_end - sub_start - 0.25, 0.25)]
            argv += ["-vn"]
            argv += [audioPath]
        else:
            argv = [self.mpvExecutable, self.filePath]
            argv += ["--include=%s" % self.mpvConf]
            argv += ["--start=%s" % secondsToTimestamp(sub_start), "--end=%s" % secondsToTimestamp(sub_end)]
            argv += ["--aid=%d" % aid]
            argv += ["--video=no"]
            argv += ["--af=afade=t=in:st=%s:d=%s,afade=t=out:st=%s:d=%s" % (sub_start, 0.25, sub_end - 0.25, 0.25)]
            argv += ["--o=%s" % audioPath]
        subprocess_calls.append(argv)
        return audio

    def get_video_filename(self, source, sub_start, sub_end, video_format):
        video = "%s_%s-%s.%s" % (format_filename(source), secondsToFilename(sub_start), secondsToFilename(sub_end), video_format)
        return video

    def subprocess_video(self, source, sub_start, sub_end, aid, aid_ff, video_format, key, subprocess_calls):
        video = self.get_video_filename(source, sub_start, sub_end, video_format)
        videoPath = os.path.join(mw.col.media.dir(), video)
        if ffmpeg_executable:
            argv = ["ffmpeg"]
            argv += ["-ss", secondsToTimestamp(sub_start)]
            argv += ["-i", self.filePath]
            argv += ["-t", secondsToTimestamp(sub_end - sub_start)]
            argv += ["-map", "0:v:0"]
            argv += ["-map", "0:a:%d" % aid_ff]
            argv += ["-af", "afade=t=in:st={:.3f}:d={:.3f},afade=t=out:st={:.3f}:d={:.3f}".format(0, 0.25, sub_end - sub_start - 0.25, 0.25)]
            argv += ["-vf", "scale=%d:%d" % (self.settings[key + "video_width"], self.settings[key + "video_height"])]
            if video_format == "webm":
                argv += ["-c:v", "libvpx-vp9"]
                argv += ["-b:v", "1400K", "-threads", "8", "-speed", "2", "-crf", "23"]
            argv += [videoPath]
        else:
            argv = [self.mpvExecutable, self.filePath]
            argv += ["--include=%s" % self.mpvConf]
            argv += ["--start=%s" % secondsToTimestamp(sub_start), "--end=%s" % secondsToTimestamp(sub_end)]
            argv += ["--sub=no"]
            argv += ["--aid=%d" % aid]
            argv += ["--af=afade=t=in:st=%s:d=%s,afade=t=out:st=%s:d=%s" % (sub_start, 0.25, sub_end - 0.25, 0.25)]
            argv += ["--vf-add=lavfi-scale=%s:%s" % (self.settings[key + "video_width"], self.settings[key + "video_height"])]
            if video_format == "webm":
                argv += ["--ovc=libvpx-vp9"]
                argv += ["--ovcopts=b=1400K,threads=4,crf=23,qmin=0,qmax=36,speed=2"]
            argv += ["--o=%s" % videoPath]
        subprocess_calls.append(argv)
        return video

    # anki.utils.call() with bundle libs if mpv is packaged
    def call(self, argv):
        if isWin:
            si = subprocess.STARTUPINFO()
            try:
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            except:
                si.dwFlags |= subprocess._subprocess.STARTF_USESHOWWINDOW
        else:
            si = None

        subprocess.Popen(argv, startupinfo=si, env=self.popenEnv)

    def addNewCard(self, timePos, timeStart, timeEnd, subText, key="", phraseMode=False):
        
        noteFields = { k:"" for k in self.configManager.getFields()}

        model = mw.col.models.byName(self.settings[key + "default_model"])
        mw.col.models.setCurrent(model)

        source = os.path.basename(self.filePath)
        source = os.path.splitext(source)[0]
        noteFields["Source"] = source

        path = os.path.basename(self.filePath)
        noteFields["Path"] = self.filePath

        note = mw.col.newNote(forDeck=False)

        sub_id = self.subsManager.get_subtitle_id(timePos)

        subTranslation = ""

        subText_before = ""
        subText_after = ""

        subTranslation_before = ""
        subTranslation_after = ""

        sub_pad_start = 0
        sub_pad_end = 0

        if sub_id is None:
            if timeStart >= 0 and timeEnd >= 0:
                noteId = "%s_%s-%s" % (format_filename(source), secondsToFilename(timeStart), secondsToFilename(timeEnd))
            else:
                noteId = "%s_%s" % (format_filename(source), secondsToFilename(timePos))
        else:
            if not phraseMode:
                sub_start, sub_end, subText = self.subsManager.get_subtitle(sub_id)
                subTranslation = self.subsManager.get_subtitle(sub_id, translation=True)[2]

                prev_sub_start, prev_sub_end, subText_before = self.subsManager.get_prev_subtitle(sub_id)
                next_sub_start, next_sub_end, subText_after = self.subsManager.get_next_subtitle(sub_id)

                subTranslation_before = self.subsManager.get_prev_subtitle(sub_id, translation=True)[2]
                subTranslation_after = self.subsManager.get_next_subtitle(sub_id, translation=True)[2]
            else:
                sub_start, sub_end, subText = self.subsManager.get_phrase(sub_id)
                subTranslation = self.subsManager.get_phrase(sub_id, translation=True)[2]

                prev_sub_start = sub_start
                next_sub_end = sub_end
            
            sub_pad_start = self.settings[key + "pad_start"] / 1000.0
            sub_pad_end = self.settings[key + "pad_end"] / 1000.0

            sub_start -= sub_pad_start
            sub_end += sub_pad_end

            prev_sub_start -= sub_pad_start
            next_sub_end += sub_pad_end

            noteId = "%s_%s-%s" % (format_filename(source), secondsToFilename(sub_start), secondsToFilename(sub_end))

        if timeStart >= 0 and timeEnd >= 0:
            sub_start = timeStart
            sub_end = timeEnd
            sub_pad_start = 0
            sub_pad_end = 0

        noteFields["Id"] = noteId
        noteFields["Line"] = subText
        noteFields["Line: before"] = subText_before
        noteFields["Line: after"] = subText_after
        noteFields["Meaning: line"] = subTranslation
        noteFields["Meaning: line before"] = subTranslation_before
        noteFields["Meaning: line after"] = subTranslation_after

        noteFields["Time"] = secondsToTimestamp(timePos)

        subprocess_calls = []

        aid = self.mpvManager.audio_id
        aid_ff = self.mpvManager.audio_ffmpeg_id
        sid = self.mpvManager.sub_id

        fieldsMap = self.fieldsMap[key + "default_model"]

        video = None

        if sub_id is not None:
            video_sub_start = sub_start
            video_sub_end = sub_end
            video_sub_pad_start = sub_pad_start
            video_sub_pad_end = sub_pad_end

        if "Image" in fieldsMap:
            image = self.subprocess_image(source, timePos, subprocess_calls)
            noteFields["Image"] = '<img src="%s" />' % image
        
        if "Image (with subtitles)" in fieldsMap:
            image_with_subtitles = self.subprocess_image(source, timePos, subprocess_calls, sub=sid, suffix="_S")
            noteFields["Image (with subtitles)"] = '<img src="%s" />' % image_with_subtitles
        
        if sub_id is not None or (timeStart >= 0 and timeEnd >= 0):
            if "Audio" in fieldsMap:
                audio = self.subprocess_audio(source, sub_start, sub_end, aid, aid_ff, subprocess_calls)
                noteFields["Audio"] = '[sound:%s]' % audio

            if "Video" in fieldsMap or "Video (HTML5)" in fieldsMap:
                video = self.subprocess_video(source, sub_start, sub_end, aid, aid_ff, "mp4", key, subprocess_calls)
                noteFields["Video"] = '[sound:%s]' % video
                noteFields["Video (HTML5)"] = video

            if "[webm] Video" in fieldsMap or "[webm] Video (HTML5)" in fieldsMap :
                video = self.subprocess_video(source, sub_start, sub_end, aid, aid_ff, "webm", key, subprocess_calls)
                noteFields["[webm] Video"] = '[sound:%s]' % video
                noteFields["[webm] Video (HTML5)"] = video

        if sub_id is not None:
            if "Audio (with context)" in fieldsMap:
                audio = self.subprocess_audio(source, prev_sub_start, next_sub_end, aid, aid_ff, subprocess_calls)
                noteFields["Audio (with context)"] = '[sound:%s]' % audio

            is_context = False

            if "Video (with context)" in fieldsMap or "Video (HTML5 with context)" in fieldsMap:
                video = self.subprocess_video(source, prev_sub_start, next_sub_end, aid, aid_ff, "mp4", key, subprocess_calls)
                noteFields["Video (with context)"] = '[sound:%s]' % video
                noteFields["Video (HTML5 with context)"] = video
                is_context = True
            
            if "[webm] Video (with context)" in fieldsMap or "[webm] Video (HTML5 with context)" in fieldsMap:
                video = self.subprocess_video(source, prev_sub_start, next_sub_end, aid, aid_ff, "webm", key, subprocess_calls)
                noteFields["[webm] Video (with context)"] = '[sound:%s]' % video
                noteFields["[webm] Video (HTML5 with context)"] = video
                is_context = True

            if is_context:
                video_sub_start = prev_sub_start
                video_sub_end = next_sub_end
                video_sub_pad_start = self.settings[key + "pad_start"] / 1000.0
                video_sub_pad_end = self.settings[key + "pad_end"] / 1000.0

            if "Video Subtitles" in fieldsMap: 
                if video is None:
                    video = self.get_video_filename(source, video_sub_start, video_sub_end, "mp4")
                subtitles = os.path.splitext(video)[0] + ".srt"
                subtitlesPath = os.path.join(mw.col.media.dir(), subtitles)
                noteFields["Video Subtitles"] = '[sound:%s]' % subtitles

        for k, v in fieldsMap.items():
            for field in v:
                note[field] = noteFields[k]

        ret = note.dupeOrEmpty()
        if ret == 2:
            if not phraseMode:
                self.mpvManager.command("show-text", "Error: Card already exists.")
            return

        did = mw.col.decks.id(self.settings[key + "default_deck"])
        note.model()['did'] = did

        if not phraseMode and mw.state == "deckBrowser":
            mw.col.decks.select(did)

        for p in subprocess_calls:
            self.call(p)

        if sub_id is not None and "Video Subtitles" in fieldsMap:
            self.subsManager.write_subtitles(video_sub_start, video_sub_end, video_sub_pad_start, video_sub_pad_end, subtitlesPath)

        cards = mw.col.addNote(note)
        if cards == 0:
            self.mpvManager.command("show-text", "Error: No cards added.")
        else:
            if isMac:
                self.mpvManager.command("expand-properties", "show-text", "Added.")
            else:
                self.mpvManager.command("expand-properties", "show-text", "${osd-ass-cc/0}{\\fscx150\\fscy150}✔")
        mw.reset()

class FieldMapping(QDialog):
    def __init__(self, name, configManager, parent=None):
        QDialog.__init__(self, parent)
        self.configManager = configManager
        self.defaultFields = self.configManager.getFields()
        self.fieldsMapping = self.configManager.getFieldsMapping(name)
        self.name = name
        self.initUI()

    def initUI(self):
        self.setWindowTitle(self.name)

        vbox = QVBoxLayout()

        self.fields = []
        groupBox = QGroupBox("Field Mapping")
        m = mw.col.models.byName(self.name)
        fields = mw.col.models.fieldNames(m)
        grid = QGridLayout()
        for idx, fld in enumerate(fields):
            le = QLineEdit(fld)
            le.setReadOnly(True)
            grid.addWidget(le, idx, 0)

            cb = QComboBox()
            cb.addItems(self.defaultFields)
            if fld in self.fieldsMapping:
                cb.setCurrentIndex(cb.findText(self.fieldsMapping[fld]))
            else:
                cb.setCurrentIndex(0)
            grid.addWidget(cb, idx, 1)

            self.fields.append((fld, cb))
        groupBox.setLayout(grid)
        vbox.addWidget(groupBox)

        self.buttonBox = QDialogButtonBox(self)
        self.buttonBox.setStandardButtons(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttonBox.setOrientation(Qt.Horizontal)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        vbox.addWidget(self.buttonBox)

        self.setLayout(vbox)

    def accept(self):
        m = {}
        for fld, cb in self.fields:
            if cb.currentText() != "<ignored>":
                m[fld] = cb.currentText()
        self.configManager.updateMapping(self.name, m)
        self.close()

class MainWindow(QDialog):
    def __init__(self, configManager, parent=None):
        QDialog.__init__(self, parent)
        self.configManager = configManager
        self.settings = self.configManager.getSettings()
        self.subsLC = {lang:lc.lower()[:2] for lang, lc in langs}
        self.initUI()

    def getTwoSpeenBoxesOptionsGroup(self, name, labels, values, options):
        groupBox = QGroupBox(name)
        spinBoxFirst = QSpinBox()
        spinBoxSecond = QSpinBox()

        grid = QGridLayout()
        
        label = QLabel(labels[0])
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(label, 0, 0)
        grid.addWidget(spinBoxFirst, 0, 1)
        grid.addWidget(QLabel(labels[2]), 0, 2)
        
        label = QLabel(labels[1])
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(label, 1, 0)
        grid.addWidget(spinBoxSecond, 1, 1)
        grid.addWidget(QLabel(labels[2]), 1, 2)

        spinBoxFirst.setRange(options[0], options[1])
        spinBoxFirst.setSingleStep(options[2])
        spinBoxFirst.setValue(values[0])

        spinBoxSecond.setRange(options[0], options[1])
        spinBoxSecond.setSingleStep(options[2])
        spinBoxSecond.setValue(values[1])

        groupBox.setLayout(grid)

        return groupBox, spinBoxFirst, spinBoxSecond

    def chooseModel(self, name):
        def onEdit():
            import aqt.models
            aqt.models.Models(mw, self)
        edit = QPushButton(_("Manage"))
        edit.clicked.connect(onEdit)
        def nameFunc():
            return sorted(mw.col.models.allNames())
        ret = StudyDeck(mw, names=nameFunc, buttons=[edit], accept=_("Choose"), title=_("Choose Note Type"), parent=self)
        if ret.name == None:
            return
        if name == "default_model":
            self.modelButton.setText(ret.name)
        elif name == "phrase_default_model":
            self.modelPhraseButton.setText(ret.name)

    def chooseDeck(self, name):
        ret = StudyDeck(mw, accept=_("Choose"), title=_("Choose Deck"), parent=self)
        if ret.name == None:
            return
        if name == "default_deck":
            self.deckButton.setText(ret.name)
        elif name == "phrase_default_deck":
            self.deckPhraseButton.setText(ret.name)

    def mapFields(self, model):
        fm = FieldMapping(model, self.configManager, parent=self)
        fm.exec_()

    def initUI(self):
        self.setWindowTitle('mpv2anki')

        vbox = QVBoxLayout()

        # Import Options

        importGroup = QGroupBox("Import Options")
        self.modelButton = QPushButton()
        if mw.col.models.byName(self.settings["default_model"]):
            self.modelButton.setText(self.settings["default_model"])
        else:
            self.modelButton.setText(mw.col.models.current()['name'])
        self.modelButton.setAutoDefault(False)
        self.modelButton.clicked.connect(lambda: self.chooseModel("default_model"))
        self.modelFieldsButton = QPushButton()
        self.modelFieldsButton.clicked.connect(lambda: self.mapFields(self.modelButton.text()))
        self.deckButton = QPushButton(self.settings["default_deck"])
        self.deckButton.clicked.connect(lambda: self.chooseDeck("default_deck"))
        self.useMPV = QCheckBox("Use MPV?")
        self.useMPV.setChecked(self.settings["use_mpv"])

        self.audio_ext = QLineEdit(self.settings["audio_ext"])

        icon = QIcon(":/icons/gears.png")
        self.modelFieldsButton.setIcon(icon)

        grid = QGridLayout()
        grid.addWidget(QLabel("Type:"), 0, 0)
        grid.addWidget(self.modelButton, 0, 1)
        grid.setColumnStretch(1, 1)
        grid.addWidget(self.modelFieldsButton, 0, 2)
        grid.addWidget(QLabel("Deck:"), 0, 3)
        grid.addWidget(self.deckButton, 0, 4)
        grid.addWidget(self.useMPV, 1, 4)
        grid.addWidget(QLabel("File ext:"), 1, 0)
        grid.addWidget(self.audio_ext, 1, 1)
        grid.setColumnStretch(4, 1)

        importGroup.setLayout(grid)
        vbox.addWidget(importGroup)

        hbox = QHBoxLayout()

        imageGroup, self.imageWidth, self.imageHeight = self.getTwoSpeenBoxesOptionsGroup("Screenshot", 
            ["Width:", "Height:", "px"], 
            [self.settings["image_width"], self.settings["image_height"]],
            [-2, 10000, 2])
        videoGroup, self.videoWidth, self.videoHeight = self.getTwoSpeenBoxesOptionsGroup("Video", 
            ["Width:", "Height:", "px"],
            [self.settings["video_width"], self.settings["video_height"]],
            [-2, 10000, 2])
        padGroup, self.padStart, self.padEnd = self.getTwoSpeenBoxesOptionsGroup("Pad Timings", 
            ["Start:", "End:", "ms"],
            [self.settings["pad_start"], self.settings["pad_end"]],
            [0, 10000, 1])

        hbox.addWidget(imageGroup)
        hbox.addWidget(videoGroup)
        hbox.addWidget(padGroup)

        grid.addLayout(hbox, 2, 0, 1, 5)

        subsGroup = QGroupBox("Subtitles")
        grid3 = QGridLayout()
        grid3.addWidget(QLabel("In your target language:"), 0, 0)
        grid3.addWidget(QLabel("In your native language:"), 1, 0)
        self.subsTargetLang = QComboBox()
        self.subsNativeLang = QComboBox()
        self.subsTargetLang.addItem("")
        self.subsNativeLang.addItem("")
        for lang, lc in langs:
            self.subsTargetLang.addItem(lang)
            self.subsNativeLang.addItem(lang)
        self.subsTargetLang.setCurrentIndex(self.subsTargetLang.findText(self.settings["subs_target_language"]))
        self.subsNativeLang.setCurrentIndex(self.subsNativeLang.findText(self.settings["subs_native_language"]))
        grid3.addWidget(self.subsTargetLang, 0, 1)
        grid3.addWidget(self.subsNativeLang, 1, 1)
        self.subsTargetLC = QLineEdit(self.settings["subs_target_language_code"])
        self.subsNativeLC = QLineEdit(self.settings["subs_native_language_code"])
        self.subsTargetLC.setFixedWidth(24)
        self.subsNativeLC.setFixedWidth(24)
        self.subsTargetLC.setReadOnly(True)
        self.subsNativeLC.setReadOnly(True)
        self.subsTargetLC.setStyleSheet("QLineEdit{background: #f4f3f4;}")
        self.subsNativeLC.setStyleSheet("QLineEdit{background: #f4f3f4;}")
        self.subsTargetLC.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.subsNativeLC.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.subsTargetLang.currentIndexChanged.connect(lambda: self.chooseSubs(self.subsTargetLang, self.subsTargetLC))
        self.subsNativeLang.currentIndexChanged.connect(lambda: self.chooseSubs(self.subsNativeLang, self.subsNativeLC))
        grid3.addWidget(self.subsTargetLC, 0, 3)
        grid3.addWidget(self.subsNativeLC, 1, 3)
        grid3.addWidget(QLabel(" (optional)"), 1, 4)
        grid3.addItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum), 0, 4, 1, 2)
        subsGroup.setLayout(grid3)

        grid.addWidget(subsGroup, 3, 0, 1, 5)

        # Import Phrases Options

        self.importPhraseGroup = QGroupBox("Generate Clips with Phrases")
        self.modelPhraseButton = QPushButton()
        if mw.col.models.byName(self.settings["phrase_default_model"]):
            self.modelPhraseButton.setText(self.settings["phrase_default_model"])
        else:
            self.modelPhraseButton.setText(mw.col.models.current()['name'])
        self.modelPhraseButton.clicked.connect(lambda: self.chooseModel("phrase_default_model"))
        self.modelPhraseFieldsButton = QPushButton()
        self.modelPhraseFieldsButton.clicked.connect(lambda: self.mapFields(self.modelPhraseButton.text()))
        self.deckPhraseButton = QPushButton(self.settings["phrase_default_deck"])
        self.deckPhraseButton.clicked.connect(lambda: self.chooseDeck("phrase_default_deck"))

        icon = QIcon(":/icons/gears.png")
        self.modelPhraseFieldsButton.setIcon(icon)

        grid = QGridLayout()
        grid.addWidget(QLabel("Type:"), 0, 0)
        grid.addWidget(self.modelPhraseButton, 0, 1)
        grid.setColumnStretch(1, 1)
        grid.addWidget(self.modelPhraseFieldsButton, 0, 2)
        grid.addWidget(QLabel("Deck:"), 0, 3)
        grid.addWidget(self.deckPhraseButton, 0, 4)
        grid.setColumnStretch(4, 1)

        self.importPhraseGroup.toggled.connect(lambda: self.toggleGroup(self.importPhraseGroup))
        self.importPhraseGroup.setLayout(grid)
        self.importPhraseGroup.setCheckable(True)
        self.importPhraseGroup.setChecked(self.settings["create_clips_with_phrases"])

        grid2 = QGridLayout()

        videoPhraseGroup, self.videoPhraseWidth, self.videoPhraseHeight = self.getTwoSpeenBoxesOptionsGroup("Video", 
            ["Width:", "Height:", "px"],
            [self.settings["phrase_video_width"], self.settings["phrase_video_height"]],
            [-2, 10000, 2])
        padPhraseGroup, self.padPhraseStart, self.padPhraseEnd = self.getTwoSpeenBoxesOptionsGroup("Pad Timings", 
            ["Start:", "End:", "ms"],
            [self.settings["phrase_pad_start"], self.settings["phrase_pad_end"]],
            [0, 10000, 1])

        gapPhraseGroup = QGroupBox("Gap between Phrases")

        h = QHBoxLayout()
        h.addItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        self.gapBetweenPhrases = QDoubleSpinBox()
        h.addWidget(self.gapBetweenPhrases)
        h.addWidget(QLabel("sec"))
        h.addItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.gapBetweenPhrases.setRange(0, 60)
        self.gapBetweenPhrases.setSingleStep(0.25)
        self.gapBetweenPhrases.setValue(self.settings["gap_between_phrases"])

        gapPhraseGroup.setLayout(h)

        grid2.addWidget(videoPhraseGroup, 0, 0, 2, 1)
        grid2.addWidget(padPhraseGroup, 0, 1, 2, 1)
        grid2.addWidget(gapPhraseGroup, 0, 2)
        
        grid.addLayout(grid2, 2, 0, 1, 5)

        # vbox.addWidget(self.importPhraseGroup)

        # Go!

        self.startButton = QPushButton("Go!")
        self.startButton.setDefault(True)
        self.startButton.clicked.connect(self.start)
        hbox = QHBoxLayout()
        hbox.addStretch(1)
        hbox.addWidget(self.startButton)
        vbox.addLayout(hbox)

        self.setLayout(vbox)

        self.setWindowIcon( QIcon(":/icons/anki.png") )

        # vbox.setSizeConstraint(QLayout.SetFixedSize)

    def chooseSubs(self, cb, cblc):
        if cb.currentText() == "":
            cblc.setText("")
        else:
            cblc.setText(self.subsLC[cb.currentText()])

    def toggleGroup(self, ctrl):
        state = ctrl.isChecked()
        if state:
            ctrl.setFixedHeight(ctrl.sizeHint().height())
        else:
            ctrl.setFixedHeight(24)
        self.adjustSize()

    def saveSettings(self):
        self.settings["default_model"] = self.modelButton.text()
        self.settings["default_deck"] = self.deckButton.text()
        self.settings["use_mpv"] = self.useMPV.isChecked()
        self.settings["image_width"] = self.imageWidth.value()
        self.settings["image_height"] = self.imageHeight.value()
        self.settings["video_width"] = self.videoWidth.value()
        self.settings["video_height"] = self.videoHeight.value()
        self.settings["pad_start"] = self.padStart.value()
        self.settings["pad_end"] = self.padEnd.value()
        self.settings["audio_ext"] = self.audio_ext.text()

        self.settings["subs_target_language"] = self.subsTargetLang.currentText()
        self.settings["subs_target_language_code"] = self.subsTargetLC.text()
        self.settings["subs_native_language"] = self.subsNativeLang.currentText()
        self.settings["subs_native_language_code"] = self.subsNativeLC.text()
        
        self.settings["create_clips_with_phrases"] = self.importPhraseGroup.isChecked()
        self.settings["phrase_default_model"] = self.modelPhraseButton.text()
        self.settings["phrase_default_deck"] = self.deckPhraseButton.text()
        self.settings["phrase_video_width"] = self.videoPhraseWidth.value()
        self.settings["phrase_video_height"] = self.videoPhraseHeight.value()
        self.settings["phrase_pad_start"] = self.padPhraseStart.value()
        self.settings["phrase_pad_end"] = self.padPhraseEnd.value()
        self.settings["gap_between_phrases"] = self.gapBetweenPhrases.value()

        self.configManager.save()

    def reject(self):
        self.saveSettings()
        self.done(0)

    def start(self):
        self.saveSettings()
        self.accept()

def openVideoWithMPV():
    executable = find_executable("mpv")
    popenEnv = os.environ.copy()

    if executable is None and isMac:
        executable = "/Applications/mpv.app/Contents/MacOS/mpv"
        if not os.path.exists(executable):
            executable = None

    if executable is None:
        mpvPath, popenEnv = _packagedCmd(["mpv"])
        executable = mpvPath[0]
    else:
        if "LD_LIBRARY_PATH" in popenEnv:
            del popenEnv['LD_LIBRARY_PATH']

    if executable == None:
        return showWarning("Please install <a href='https://mpv.io'>mpv</a> and add it to the PATH environment variable on Windows.", parent=mw)

    configManager = ConfigManager()
    mainWindow = MainWindow(configManager, parent=mw)

    if mainWindow.exec_():
        filePath = getVideoFile()
        if not filePath:
            return

        subsManager = SubtitlesHelper(filePath, configManager)
        if subsManager.status_code != "error":
            AnkiHelper(executable, popenEnv, filePath, configManager, subsManager)

    mw.reset()


action = QAction("Open Video...", mw)
action.setShortcut(_("Ctrl+O"))
action.triggered.connect(openVideoWithMPV)
mw.form.menuTools.addAction(action)
