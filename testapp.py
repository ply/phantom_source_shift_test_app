#!/usr/bin/env python3

import wx, sys, os, shutil, datetime, configparser, json, random
import traceback
import pyaudio, soundfile


class Player:
    DTYPE = 'float32'
    SAMPLE_FORMAT = pyaudio.paFloat32

    def __init__(self, pyaudio_instance):
        self.pyAudio = pyaudio_instance
        self.stream = None
        self.__audio_data = None
        self.__position = 0

    def __del__(self):
        if self.stream is not None:
            self.stream.close()

    def play(self, filename=None, devid=None):
        if filename is None:
            filename = self.__filename

        if devid is None:
            devid = self._devid
        else:
            self._devid = devid

        if self.stream is not None:
            self.stream.stop_stream()

        if self.__audio_data is None or self.__filename != filename:
            self.__filename = filename
            if self.stream is not None:
                self.stream.close()
            self.__position = 0
            sndfile = soundfile.SoundFile(filename, 'r')
            self.__audio_data = sndfile.read(always_2d=True, dtype=self.DTYPE).tobytes()
            sndfile.close()
            self.stream = self.pyAudio.open(sndfile.samplerate,
                                            sndfile.channels,
                                            self.SAMPLE_FORMAT,
                                            output=True,
                                            output_device_index=devid,
                                            stream_callback=self._callback)
            self._bytes_per_frame = pyaudio.get_sample_size(self.SAMPLE_FORMAT) * sndfile.channels
        else:
            self.stream.stop_stream()
            self.__position = 0
            self.stream.start_stream()

    def _callback(self, _in_data, frame_count, _time_info, _status):
        frame_size = frame_count * self._bytes_per_frame
        end = self.__position + frame_size
        data = self.__audio_data[self.__position:end]
        data.ljust(frame_size, b'\0') # zero-padding
        if end < len(self.__audio_data):
            code = pyaudio.paContinue
            self.__position = end
        else:
            code = pyaudio.paComplete
            self.__position = 0
        return data, code


class TestScheme:
    class _Set:
        def __init__(self, name, content):
            self.name = name
            self.content = content

    class _Example:
        def __init__(self, name, path):
            self.name = name
            self.path = path

    def __init__(self, filename):
        basedir = os.path.dirname(filename)
        parser = configparser.ConfigParser(allow_no_value=True)
        parser.read(filename)

        # configuration
        config = parser['config']
        self.samples_dir = os.path.join(
            basedir, config.get('samples-dir', fallback=''))
        self.test_sample = os.path.join(self.samples_dir, config['test-sample'])
        self.results_dir = os.path.join(basedir, config['results-dir'])

        randomize = config.getboolean('randomize', fallback=False)
        self.description = config.get('description', fallback='')
        try:
            if config['debug'] is None:
                self.debug = True
            else:
                self.debug = config.getboolean('debug')
        except KeyError:
            self.debug = False

        # test
        self.test = list()
        for name in filter(lambda x: x != 'config', parser.sections()):
            samples = tuple(map(
                lambda k: self._Example(k, os.path.join(self.samples_dir, k)),
                parser[name].keys()))
            if randomize and name.lower() != 'anchors':
                samples = tuple(random.sample(samples, len(samples)))
            self.test.append(self._Set(name, samples))


class ResultsHandler:
    def __init__(self, filename):
        if not os.path.isdir(os.path.dirname(filename)):
            raise NotADirectoryError(
                "{} nie istnieje lub nie jest katalogiem"
                    .format(os.path.dirname(filename)))
        if os.path.exists(filename):
            raise FileExistsError
        self.f = open(filename, 'w')
        self.parser = configparser.ConfigParser(interpolation=None)
        self.parser.add_section('metadata')

    def __setitem__(self, k, v):
        self.set_meta({k: v})

    def set_meta(self, dictionary, save=True):
        self.parser['metadata'].update({k: str(v) for k, v in dictionary.items()})
        if save:
            self.save()

    def save(self):
        shutil.copyfile(self.f.name, "{}.bak".format(self.f.name))
        self.f.seek(0)
        self.f.truncate()
        self.parser.write(self.f)

    def submit(self, set, sample, answer, comment, time, playcount):
        if not self.parser.has_section(set):
            self.parser.add_section(set)
        self.parser.set(set, sample, json.dumps(dict(
            answer=answer,
            comment=comment,
            time=time,
            playcount=playcount
        )))
        self.save()

    def __del__(self):
        try:
            self.save()
            self.f.close()
        except AttributeError:
            pass


class SetupFrame(wx.Frame, Player):
    def __init__(self, parent, pyaudio_instance):
        wx.Frame.__init__(self, parent, title="Ustawienia")
        Player.__init__(self, pyaudio_instance)

        # get PyAudio info
        p = pyaudio_instance
        self.devices = tuple(filter(
            lambda d: d['maxOutputChannels'] >= 2,
            [p.get_device_info_by_index(i) for i in range(p.get_device_count())]
        ))
        api_names = tuple(p.get_host_api_info_by_index(i)['name']
                          for i in range(p.get_host_api_count()))

        # configuration handling
        self.appconfig = configparser.ConfigParser()
        self.appconffile = os.path.abspath(sys.argv[0] + '.ini') if sys.argv[0] else ()
        self.appconfig.read(self.appconffile)

        # controls needed to be accessible from other functions
        panel = wx.Panel(self)
        self.devCtl = wx.Choice(panel, choices=['{} ({})'.format(d['name'], api_names[d['hostApi']])
                                               for d in self.devices])
        self.devCtl.SetStringSelection(self.appconfig.get("setup", "audio-device", fallback=''))
        self.inCtl = wx.FilePickerCtrl(panel,
                                       path=self.appconfig.get("setup", "test-config",
                                                               fallback="test.ini"),
                                       wildcard='*.ini')

        # parameter controls with labels
        params = {
            "Urządzenie": self.devCtl,
            # "Częstotliwość próbkowania" : None,
            "Plik konfiguracji testu": self.inCtl,
        }

        # buttons with their actions
        buttons = {
            wx.Button(panel, label="Sprawdź ustawienia"): self.on_check,
            wx.Button(panel, label="OK"): self.on_ok,
        }

        # necessary for Cmd-Q shortcut to function
        # (but works in next window as well, without being asked)
        #self.menubar = wx.MenuBar()
        #self.SetMenuBar(self.menubar)

        # create layout and show window
        grid = wx.FlexGridSizer(2, 0, 5)
        grid.AddGrowableCol(1)
        grid.SetFlexibleDirection(wx.HORIZONTAL)
        for k, v in params.items():
            grid.Add(wx.StaticText(panel, label=k), 1, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(v, 2, wx.EXPAND)
        vbox = wx.BoxSizer(wx.VERTICAL)
        vbox.Add(grid, 1, wx.ALL | wx.EXPAND, border=10)
        for k, v in buttons.items():
            vbox.Add(k, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, border=10)
            self.Bind(wx.EVT_BUTTON, v, k)
        vbox.AddSpacer(10)
        panel.SetSizer(vbox)
        vbox.SetSizeHints(self)
        self.Show(True)

    def on_check(self, _event):
        pa_dev_id = self.devices[self.devCtl.GetSelection()]["index"]
        filename = TestScheme(self.inCtl.GetPath()).test_sample
        self.play(filename, pa_dev_id)

    def on_ok(self, _event):
        # save settings as default
        if self.appconffile:
            self.appconfig['setup'] = {
                "audio-device": self.devCtl.GetStringSelection(),
                "test-config": self.inCtl.GetPath(),
            }
            with open(self.appconffile, 'w') as f:
                self.appconfig.write(f)
        # close current window and run test (new Frame)
        devid = self.devices[self.devCtl.GetSelection()]["index"]
        filename = self.inCtl.GetPath()
        self.Close(True)
        RunTestFrame(self.GetParent(), self.pyAudio, devid, filename)


class RunTestFrame(wx.Frame, Player):
    def __init__(self, parent, pyaudio_instance, pyaudio_device_id, test_config_file):
        wx.Frame.__init__(self, parent, title="Test")
        Player.__init__(self, pyaudio_instance)

        self.devID = pyaudio_device_id
        self.results = dict()
        self.current_set = 0
        self.current_example = -1

        # load config file
        self.scheme = TestScheme(test_config_file)

        today = datetime.datetime.today()
        # open results file
        outfilename = os.path.join(
            self.scheme.results_dir,
            today.strftime("%Y-%m-%d-T-%H-%M-%S.txt"))
        try:
            self.results = ResultsHandler(outfilename)
        except FileExistsError:
            wx.MessageDialog(self, "Plik istnieje: {}".format(outfilename),
                             caption="Błąd",
                             style=wx.ICON_ERROR | wx.OK
                             ).ShowModal()
            self.Close(True)
            return
        except RuntimeError:
            wx.MessageDialog(self, format(sys.exc_info()[1]),
                             caption="Błąd zapisu pliku wyników",
                             style=wx.ICON_ERROR | wx.OK
                             ).ShowModal()
            self.Close(True)
            return

        # get user name
        dialog = wx.TextEntryDialog(self, "Jak się nazywasz?", style=wx.OK)
        dialog.ShowModal()
        self.results.set_meta({
            'name': dialog.GetValue(),
            'test_filename': test_config_file,
            'test_description': self.scheme.description,
            'started': today.isoformat(timespec='seconds'),
            'completed': False,
        })
        dialog.Destroy()

        # prepare layout
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        vbox_add = lambda w, style=0: \
            vbox.Add(w, 0, wx.EXPAND|wx.LEFT|wx.RIGHT|style, border=5)

        # add controls
        self.label = wx.StaticText(panel, style=wx.ALIGN_CENTRE_HORIZONTAL)
        if self.scheme.debug:
            self.label.Bind(wx.EVT_LEFT_DOWN,
                            lambda e: self.set_label(
                                self.scheme.test[self.current_set].content[self.current_example].name
                            ))
            self.label.Bind(wx.EVT_LEFT_UP,
                            lambda e: self.set_label()
                            )
        vbox_add(self.label, wx.ALL)
        self.play_btn = wx.Button(panel, label="&Odtwórz")
        self.Bind(wx.EVT_BUTTON, lambda e: self.play(), self.play_btn)
        vbox_add(self.play_btn, wx.ALL)
        vbox_add(wx.StaticText(panel, label='C', style=wx.ALIGN_CENTRE_HORIZONTAL),
                 wx.TOP)
        self.slider = wx.Slider(panel, value=0, minValue=-100, maxValue=100)
        self.slider.SetSizeHints(402, self.slider.GetMinHeight())
        self.slider.Bind(wx.EVT_LEFT_DCLICK, self.reset_slider)
        self.slider.Bind(wx.EVT_RIGHT_DOWN, self.reset_slider)
        vbox_add(self.slider, wx.BOTTOM)
        vbox_add(wx.StaticText(panel, label="Komentarz (opcjonalny):"), wx.ALL)
        self.comment = wx.TextCtrl(panel)
        vbox_add(self.comment, wx.ALL)
        self.enter = wx.Button(panel, label="&Zatwierdź")
        self.enter.SetDefault()
        self.Bind(wx.EVT_BUTTON, self.on_confirm, self.enter)
        vbox_add(self.enter, wx.ALL)
        panel.SetSizerAndFit(vbox)

        hbox = wx.BoxSizer(wx.HORIZONTAL)
        hbox.Add(panel, 1, wx.ALL | wx.EXPAND, border=5)
        self.SetSizerAndFit(hbox)
        self.Centre()

        # safe closing
        self.Bind(wx.EVT_CLOSE, self.on_close)
        #self.Bind(wx.EVT_MENU, self.on_close, id=wx.ID_EXIT)

        # run test
        self.next_example()
        self.Show()

    def play(self, *args):
        self.play_count += 1
        super().play(*args)

    def next_example(self):
        """Loads next example. Finalizes test if there is no examples left."""

        # move indexes to next example or quit
        self.current_example += 1
        if self.current_example == len(self.scheme.test[self.current_set].content):
            self.current_set += 1
            self.current_example = 0
            if self.current_set == len(self.scheme.test):
                self.finalize()
                return

        # reset interface
        self.set_label()
        self.reset_slider()
        self.play_btn.SetFocus()
        self.slider.SetFocus()
        self.comment.SetValue('')

        # set internals and play
        self.play_count = 0
        self.example_started = datetime.datetime.now()
        self.play(self.scheme.test[self.current_set].content[self.current_example].path, self.devID)

    def set_label(self, str=None):
        if str is None:
            str = "zestaw {}, przykład {}".format(self.current_set, self.current_example)
        self.label.SetLabelText(str)
        self.label.PostSizeEventToParent()

    def on_confirm(self, _event):
        self.results.submit(
            self.scheme.test[self.current_set].name,
            self.scheme.test[self.current_set].content[self.current_example].name,
            self.slider.GetValue(),
            self.comment.GetValue(),
            (datetime.datetime.now() - self.example_started).total_seconds(),
            self.play_count
        )
        self.next_example()

    def reset_slider(self, _event=None):
        self.slider.SetValue(0)

    def on_close(self, event):
        if wx.MessageDialog(self, "Czy na pewno chcesz przerwać test?",
                            style=wx.ICON_QUESTION | wx.YES_NO
                            ).ShowModal() == wx.ID_YES:
            self.Destroy()

    def finalize(self):
        self.results['completed'] = datetime.datetime.today().isoformat(timespec='seconds')
        wx.MessageDialog(self, "Dziękuję za udział w badaniach!",
                         caption="Koniec testu").ShowModal()
        self.Destroy()

def wx_exception_handler(app):
    def handler(exc_type, exc_value, tb):
        traceback.print_exception(exc_type, exc_value, tb)
        wx.MessageDialog(app.TopWindow,
                         '{}\n\n{}'.format(exc_value,
                                           ''.join(traceback.format_tb(tb))),
                         exc_type.__name__,
                         wx.OK | wx.ICON_ERROR
                         ).ShowModal()
    return handler

def main():
    p = pyaudio.PyAudio()
    app = wx.App(False)
    sys.excepthook = wx_exception_handler(app)
    SetupFrame(None, p)
    app.MainLoop()
    p.terminate()

if __name__ == '__main__':
    main()
