import cv2
import warnings
from collections import deque
from datetime import datetime

import numpy as np
from PyQt5 import QtCore
from PyQt5.QtCore import pyqtSignal
from matplotlib.dates import date2num

from VideoRecording import VideoRecording

__author__ = 'Fabian Sinz, Joerg Henninger'


def brg2rgb(frame):
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def brg2grayscale(frame):
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


class Camera(QtCore.QObject):
    # signals
    sig_new_frame = pyqtSignal()
    sig_start_rec = pyqtSignal()
    sig_set_timestamp = pyqtSignal(object)
    sig_raise_error = pyqtSignal(object)

    def __init__(self, control, device_no=0, post_processor=None, parent=None):
        """
        Initializes a new camera
        :param post_processor: function that is applies to the frame after grabbing
        """
        QtCore.QObject.__init__(self, parent)
        self.mutex = QtCore.QMutex()

        self.control = control
        self.filename = 'video'
        self.triggered = False
        self.capture = None
        self.device_no = device_no
        self.name = None
        self.recording = None
        self.post_processor = post_processor
        if post_processor is None:
            self.post_processor = lambda *args: args

        self.width = self.control.cfg["video_xy"][0]
        self.height = self.control.cfg["video_xy"][1]
        self.framerate = self.control.cfg["video_fps"]
        self.color = self.control.cfg["video_color"]

        self.saving = False

        self.frame_dts = deque()
        self.recframes = deque()
        self.dispframe = None

        self.open()
        self.empty_frame = np.zeros((np.int(self.height), np.int(self.width), np.int(3)))
        if self.color:
            self.empty_frame = self.empty_frame[:, :, 0]

        self.sig_set_timestamp.connect(self.control.set_timestamp)
        self.sig_raise_error.connect(self.control.raise_error)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, type, value, traceback):
        self.close()

    def open(self):

        self.capture = cv2.VideoCapture(self.device_no)

        """ set fps, width, height and turn autofocus off """
        self.capture.set(5, self.framerate)
        self.capture.set(3, self.width)
        self.capture.set(4, self.height)
        self.capture.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        pass

    def is_working(self):
        return self.capture.isOpened()

    def get_properties(self):
        """
        :returns: the properties (cv2.CAP_PROP_*) from the camera
        :rtype: dict
        """
        if self.capture is not None:
            properties = [e for e in dir(cv2) if "CAP_PROP" in e]
            ret = {}
            for e in properties:
                ret[e[12:].lower()] = self.capture.get(getattr(cv2, e))
            return ret
        else:
            warnings.warn("Camera needs to be opened first!")
            return None

    def get_resolution(self):
        if self.capture is not None:
            return int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH)), \
                   int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        else:
            raise ValueError("Camera is not opened or not functional! Capture is None")

    def get_fps(self):
        if self.capture is not None:
            return int(self.capture.get(cv2.CAP_PROP_FPS))
        else:
            raise ValueError("Camera is not opened yet")

    def get_dispframe(self):
        self.mutex.lock()
        dispframe = self.dispframe
        self.mutex.unlock()
        self.dispframe = None
        return dispframe

    def get_recframe(self):
        self.mutex.lock()
        if len(self.recframes):
            recframe = self.recframes.popleft()
            # print(len(self.recframes))
            self.mutex.unlock()
            return recframe
        else:
            self.mutex.unlock()
            return None

    def get_recframesize(self):
        self.mutex.lock()
        s = len(self.recframes)
        # print(len(self.recframes))
        self.mutex.unlock()
        return s

    def grab_frame(self, saving=False):
        # grab frame
        flag, frame = self.capture.read()
        dtime = datetime.now()

        # calculate framerate
        self.frame_dts.append(dtime)
        if len(self.frame_dts) > 100:
            self.frame_dts.popleft()
        if len(self.frame_dts) > 1:
            dur = (self.frame_dts[-1] - self.frame_dts[0]).total_seconds()
            fr = len(self.frame_dts) / dur if dur > 0 else 0
        else:
            fr = 0

        # post-processing
        try:
            if self.color:
                frame = brg2rgb(frame)
                self.empty_frame = frame
            else:
                frame = brg2grayscale(frame)
                self.empty_frame = frame
        except:
            if self.color:
                frame = self.empty_frame
            else:
                frame = self.empty_frame

        # DEBUG
        # gap = 1000.*(dtime - self.last_frame).total_seconds()
        # if self.min > gap:
        #     self.min = gap
        # sys.stdout.write('\rframerate: {0:3.2f} ms{1:s}; min:{2:3.2f}'.format(gap, 5*' ', self.min,5*' '))
        # sys.stdout.flush()
        # self.last_frame = dtime

        self.write_to_frame(frame, dtime.strftime("%Y-%m-%d %H:%M:%S"), int(0.05*self.width), int(0.05*self.height))

        if self.control.options.remote:
            self.write_to_frame(frame, str(self.control.main.remote_layout.speed) + " m/s", int(0.85 * self.width), int(0.05 * self.height))

        if not flag:
            warnings.warn("Couldn't grab frame from camera!")
            return None

        # store frames for other threads
        self.mutex.lock()
        self.dispframe = (frame, dtime, fr)
        self.mutex.unlock()

        if self.is_saving():
            self.mutex.lock()
            dtime = '{:.10f}\n'.format(date2num(dtime))  # change datetime format to float
            self.recframes.append((frame, dtime))
            self.mutex.unlock()
            # emit signal for recording thread
            self.sig_new_frame.emit()

    def write_to_frame(self, frm, text, where_x, where_y):

        # cv2.rectangle(frm, (0, 0), (int(self.width-1), int(self.height*0.15)), (200, 200, 200), cv2.FILLED)
        cv2.putText(frm, text, (where_x, where_y), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
        return frm

    def new_recording(self, save_dir, cam_name, file_counter, framerate=0):

        if not self.triggered:
            # framerate = self.framerate
            try:
                framerate = self.get_dispframe()[2] ## get real frame rate to encoder
            except:
                framerate = self.framerate
                print('FPS was not successfully read out and set')

        self.recording = VideoRecording(self, save_dir, cam_name, file_counter,
                                        self.get_resolution(),
                                        framerate, color=self.color)

        pass
        if not self.recording.isOpened():
            error = 'Video-recording could not be started.'
            self.sig_raise_error.emit(error)
            return False

        self.recordingThread = QtCore.QThread()
        self.recording.moveToThread(self.recordingThread)
        self.recordingThread.start()
        self.sig_new_frame.connect(self.recording.write)

    def is_recording(self):
        self.mutex.lock()
        c = self.continuous
        self.mutex.unlock()
        return c

    def is_saving(self):
        self.mutex.lock()
        sav = self.saving
        self.mutex.unlock()
        return sav

    def stop_recording(self):
        self.mutex.lock()
        self.continuous = False
        self.mutex.unlock()

    def start_capture(self):
        """ for continuous frame acquisition """
        self.continuous = True
        while self.is_recording():
            self.grab_frame()

    def stop_capture(self):
        self.mutex.lock()
        self.continuous = False
        self.mutex.unlock()

    def start_saving(self):
        self.mutex.lock()
        self.saving = True
        self.mutex.unlock()

    # def stop_saving(self, triggered_frames):
    def stop_saving(self):
        self.mutex.lock()
        self.saving = False
        self.sig_new_frame.disconnect(self.recording.write)
        self.mutex.unlock()

        last = self.recording.get_write_count()
        double_counter = -1
        #TODO: fix triggered frames properly
        triggered_frames = 0
        while self.get_recframesize() > 0:
            print('Writing: {} of {}'.format(self.recording.get_write_count(), triggered_frames))
            print(self.get_recframesize())
            if last == self.recording.get_write_count(): double_counter += 1
            if double_counter == 10:
                error = 'Frames cannot be saved.'
                self.sig_raise_error.emit(error)
                break
            QtCore.QThread.msleep(100)

        # wait until all frames are written, then close the recording
        # if triggered_frames == self.recording.get_write_count():
        timestamp = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        s = timestamp + ' \t ' + 'All frames written: '
        s += '{} of {}'.format(self.recording.get_write_count(), triggered_frames)
        self.sig_set_timestamp.emit(s)

        self.recording.release()
        self.recordingThread.quit()
        self.recordingThread.wait()
        self.recording = None
        self.recordingThread = None

    def close(self):
        # release camera
        self.capture.release()
        pass
