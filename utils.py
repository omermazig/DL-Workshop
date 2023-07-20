from datetime import datetime, timedelta

import cv2
import logging
import pytesseract
import time
from contextlib import contextmanager
from json import JSONDecodeError
from tenacity import retry, stop_after_attempt, wait_random, retry_if_exception_type, before_sleep_log
from typing import Dict

from nba_api.stats.endpoints import playbyplayv2, videoeventsasset

logger = logging.getLogger(__name__)


class ActionGapManager:
    def __init__(self, gap=0.6):
        self.gap = gap
        self.last_action_time = None

    def _wait_for_gap(self):
        if self.last_action_time is not None:
            elapsed_time = time.time() - self.last_action_time
            if elapsed_time < self.gap:
                time.sleep(self.gap - elapsed_time)

    def _update_last_action_time(self):
        self.last_action_time = time.time()

    @contextmanager
    def action_gap(self):
        try:
            self._wait_for_gap()
            yield
        finally:
            self._update_last_action_time()


# This is for not overloading the NBA API and getting blocked
nba_api_cooldown = 0.6
gap_manager = ActionGapManager(gap=nba_api_cooldown)


def get_pbp_data(game_id):
    with gap_manager.action_gap():
        raw_data = playbyplayv2.PlayByPlayV2(game_id=game_id, timeout=60 * 5)
    df = raw_data.get_data_frames()[0]
    return df


@retry(stop=stop_after_attempt(50), wait=wait_random(min=1, max=2),
       retry=retry_if_exception_type((JSONDecodeError, ConnectionError)), reraise=True,
       before_sleep=before_sleep_log(logger, logging.DEBUG))
def get_video_event_dict(game_id: str, game_event_id: str) -> Dict:
    with gap_manager.action_gap():
        raw_data = videoeventsasset.VideoEventsAsset(game_id=game_id, game_event_id=str(game_event_id), timeout=5 * 60)
    json = raw_data.get_dict()
    return json


def cut_video(video_path: str, start_time: str, cut_duration: int, output_path: str):
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)

    try:
        # Variables to track video recording
        recording = False
        frames_to_record = int(cut_duration * fps)
        current_frames = 0
        new_video_current_frames = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                # Video has ended
                break

            if not recording:
                # Check text condition every 1 second
                if current_frames % fps == 0:
                    # Get height, width, and channels from the frame.shape tuple
                    height, width, channels = frame.shape
                    # Crop the bottom third of the frame
                    crop_img = frame[height - height // 3:height, 0:width]

                    # Continue with your image processing steps...
                    gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
                    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
                    blurred = cv2.GaussianBlur(bw, (5, 5), 0)
                    text_data = pytesseract.image_to_string(blurred, lang='eng', config='--psm 11')

                    # Check if the condition is met and we should start recording
                    if start_time in text_data:
                        # Set recording
                        recording = True
                        # Initialize the video writer to save the cut video
                        fourcc = cv2.VideoWriter_fourcc(*'XVID')
                        out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
            else:
                # Write the frame to the cut video
                out.write(frame)
                new_video_current_frames += 1

                # Check if the required number of frames have been recorded
                if new_video_current_frames >= frames_to_record:
                    recording = None # Signal that we're done recording
                    out.release()

            current_frames += 1

            # # Display the processed frame (optional)
            # cv2.imshow("Processed Frame", blurred)

            # Press 'q' to quit
            if cv2.waitKey(1) & 0xFF == ord('q') or recording is None:
                break

    finally:
        # Release the video capture and close all windows
        cap.release()
        cv2.destroyAllWindows()


def add_seconds_to_time(time_str, seconds_to_add):
    # Parse the input time string into a datetime object
    time_format = "%M:%S"
    time_obj = datetime.strptime(time_str, time_format)

    if time_obj.minute != 0:
        # Regular case. There is what to decrease from
        new_time_obj = time_obj + timedelta(seconds=seconds_to_add)
    else:
        # Spacial case. Need to make sure we don't go below 0
        new_time_obj = time_obj + timedelta(seconds=max(seconds_to_add, -time_obj.second))

    # Check if the time is under a minute
    if new_time_obj.minute == 0:
        # Format the result as seconds only (without leading zeros)
        result_time_str = f'{new_time_obj.strftime("%S")}.'
    else:
        # Format the result as minutes and seconds
        result_time_str = new_time_obj.strftime("%M:%S")

    # Remove leading zero from minutes (if present)
    if result_time_str.startswith('0'):
        result_time_str = result_time_str[1:]

    return result_time_str
