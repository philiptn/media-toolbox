#!/usr/bin/env python3

import argparse
import random
import tkinter as tk
from tkinter import Label, Canvas, Button, Entry
import cv2
from PIL import Image, ImageTk
import os

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Video Cropping Preview"
    )
    parser.add_argument("--file", type=str, required=True, help="Path to a video file.")
    return parser.parse_args()

def snap_to_even(value):
    """Snap to the nearest even integer."""
    return 2 * round(value / 2)

class CropGUI:
    def __init__(self, master, cap):
        self.master = master
        self.cap = cap

        # Video info
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        if not self.fps or self.fps <= 0:
            self.fps = 30.0  # fallback

        self.current_frame_index = 0

        # Raw frame data
        self.raw_frame_bgr = None
        self.raw_width = 0
        self.raw_height = 0

        # Display size
        self.display_width = 0
        self.display_height = 0

        # Build UI
        self.main_frame = tk.Frame(master)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = Canvas(self.main_frame, bg="gray")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Row for timecode
        self.tc_frame = tk.Frame(self.main_frame)
        self.tc_frame.pack(pady=2)

        Label(self.tc_frame, text="                  Timecode:", font=("Arial", 12)).pack(side=tk.LEFT, padx=4)

        self.timecode_entry = Entry(self.tc_frame, width=12)
        self.timecode_entry.pack(side=tk.LEFT, padx=5)
        # Press Enter in timecode field => parse & seek
        self.timecode_entry.bind("<Return>", self.on_timecode_enter)

        # Row for crop offsets
        self.crop_frame = tk.Frame(self.main_frame)
        self.crop_frame.pack(pady=2)

        Label(self.crop_frame, text="Crop (left,right,top,bottom):", font=("Arial", 12)).pack(side=tk.LEFT, padx=4)

        self.crop_entry = Entry(self.crop_frame, width=18)
        self.crop_entry.pack(side=tk.LEFT, padx=5)
        # Press Enter in crop field => parse & apply
        self.crop_entry.bind("<Return>", self.on_crop_enter)

        # Button: random frame
        self.new_frame_button = Button(self.main_frame, text="Get new frame", command=self.pick_new_frame)
        self.new_frame_button.pack(pady=5)

        # Dragging corners/edges
        self.active_drag = None  # ("corner", idx) or ("edge", top/left/..)
        self.corner_hit_size = 10
        self.edge_hit_size = 8

        # Canvas items
        self.tk_image = None
        self.image_on_canvas = None
        self.rect_id = None

        # Crop coords in raw domain (left, top, right, bottom)
        self.crop_coords = None  # no reset on new frames

        # Start up
        self.pick_new_frame()

        # Bind events
        self.canvas.bind("<Button-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<Configure>", self.on_canvas_resize)

    # -------------------------------------------------------------------------
    # TIME / FRAME
    # -------------------------------------------------------------------------
    def pick_new_frame(self):
        """Pick a random frame between 20% and 80% of total frames."""
        if self.total_frames <= 0:
            return
        start_f = int(self.total_frames * 0.2)
        end_f   = int(self.total_frames * 0.8)
        new_frame = random.randint(start_f, end_f)
        self.load_frame(new_frame)

    def on_timecode_enter(self, event):
        """User pressed Enter in timecode_entry => parse & load."""
        text = self.timecode_entry.get().strip()
        frame_idx = self._parse_timecode_to_frame(text)
        if frame_idx is None:
            print(f"Invalid timecode: '{text}'")
            return
        self.load_frame(frame_idx)

    def load_frame(self, frame_index):
        """Seek to frame_index, clamp if out of range, read, update UI."""
        if frame_index < 0:
            frame_index = 0
        if self.total_frames > 0 and frame_index >= self.total_frames:
            frame_index = self.total_frames - 1

        self.current_frame_index = frame_index
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ret, frame_bgr = self.cap.read()
        if not ret or frame_bgr is None:
            print(f"Failed to read frame {frame_index}")
            return

        self.raw_frame_bgr = frame_bgr
        self.raw_height, self.raw_width, _ = frame_bgr.shape

        # If no crop yet, full frame
        if self.crop_coords is None:
            self.crop_coords = [0, 0, self.raw_width, self.raw_height]
        else:
            self._clamp_crop()

        # Update timecode entry
        tc_str = self._frame_to_timecode(self.current_frame_index)
        self.timecode_entry.delete(0, tk.END)
        self.timecode_entry.insert(0, tc_str)

        self._render_scaled_image()
        self._update_crop_entry()

    # -------------------------------------------------------------------------
    # CROP OVERRIDE
    # -------------------------------------------------------------------------
    def on_crop_enter(self, event):
        """User pressed Enter in crop_entry => parse & apply offsets."""
        text = self.crop_entry.get().strip()
        parts = text.split(",")
        if len(parts) != 4:
            print(f"Invalid crop offsets: '{text}'. Need 4 integers: left,right,top,bottom.")
            return
        try:
            left_off   = int(parts[0])
            right_off  = int(parts[1])
            top_off    = int(parts[2])
            bottom_off = int(parts[3])
        except ValueError:
            print(f"Invalid crop offsets: '{text}'. Non-integer encountered.")
            return

        # Convert offsets => bounding box
        raw_left   = left_off
        raw_right  = self.raw_width - right_off
        raw_top    = top_off
        raw_bottom = self.raw_height - bottom_off

        # clamp
        if raw_left < 0: raw_left = 0
        if raw_top < 0:  raw_top  = 0
        if raw_right > self.raw_width:
            raw_right = self.raw_width
        if raw_bottom > self.raw_height:
            raw_bottom = self.raw_height
        if raw_left >= raw_right:
            raw_right = raw_left + 2
        if raw_top >= raw_bottom:
            raw_bottom = raw_top + 2

        self.crop_coords = [raw_left, raw_top, raw_right, raw_bottom]
        self._render_scaled_image()
        self._update_crop_entry()

    # -------------------------------------------------------------------------
    # CLAMP / TIME UTILS
    # -------------------------------------------------------------------------
    def _clamp_crop(self):
        l, t, r, b = self.crop_coords
        if l < 0: l = 0
        if t < 0: t = 0
        if r > self.raw_width:  r = self.raw_width
        if b > self.raw_height: b = self.raw_height
        if l >= r:
            r = l + 2
        if t >= b:
            b = t + 2
        self.crop_coords = [l, t, r, b]

    def _frame_to_timecode(self, frame_idx):
        """Convert frame_idx -> HH:MM:SS.mmm."""
        if self.fps <= 0:
            return "00:00:00.000"
        total_seconds = frame_idx / self.fps
        hh = int(total_seconds // 3600)
        remainder = total_seconds % 3600
        mm = int(remainder // 60)
        ss = remainder % 60

        s_int = int(ss)
        frac = ss - s_int
        ms = int(round(frac * 1000))

        return f"{hh:02d}:{mm:02d}:{s_int:02d}.{ms:03d}"

    def _parse_timecode_to_frame(self, text):
        """Parse HH:MM:SS.mmm -> frame index, or None if invalid."""
        parts = text.split(":")
        if len(parts) != 3:
            return None
        try:
            hh = int(parts[0])
            mm = int(parts[1])
        except ValueError:
            return None

        sec_parts = parts[2].split(".")
        if len(sec_parts) == 1:
            # no ms
            ss = int(sec_parts[0])
            ms = 0
        elif len(sec_parts) == 2:
            ss = int(sec_parts[0])
            ms = int(sec_parts[1])
        else:
            return None

        total_sec = hh*3600 + mm*60 + ss + (ms/1000.0)
        frame_f = total_sec * self.fps
        return int(round(frame_f))

    # -------------------------------------------------------------------------
    # CANVAS / RENDERING
    # -------------------------------------------------------------------------
    def on_canvas_resize(self, event):
        self._render_scaled_image()

    def _render_scaled_image(self):
        """Scale raw_frame_bgr to fit the canvas, preserving aspect. Then draw rectangle."""
        if self.raw_frame_bgr is None:
            return
        cwidth  = self.canvas.winfo_width()
        cheight = self.canvas.winfo_height()
        if cwidth < 1 or cheight < 1:
            return

        frame_aspect = self.raw_width / self.raw_height if self.raw_height else 1
        canvas_aspect = cwidth / cheight if cheight else 1

        if canvas_aspect > frame_aspect:
            scale = cheight / self.raw_height
        else:
            scale = cwidth / self.raw_width

        self.display_width  = max(1, int(self.raw_width  * scale))
        self.display_height = max(1, int(self.raw_height * scale))

        resized_bgr = cv2.resize(
            self.raw_frame_bgr, (self.display_width, self.display_height), interpolation=cv2.INTER_LINEAR
        )
        resized_rgb = cv2.cvtColor(resized_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(resized_rgb)
        self.tk_image = ImageTk.PhotoImage(pil_img)

        if self.image_on_canvas is None:
            self.image_on_canvas = self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_image)
        else:
            self.canvas.itemconfig(self.image_on_canvas, image=self.tk_image)

        offset_x = (cwidth - self.display_width)//2
        offset_y = (cheight - self.display_height)//2
        self.canvas.coords(self.image_on_canvas, offset_x, offset_y)

        # Rectangle
        if self.rect_id is None:
            self.rect_id = self.canvas.create_rectangle(
                offset_x, offset_y, offset_x+10, offset_y+10,
                outline="red", width=2
            )
        if self.crop_coords:
            l, t, r, b = self.crop_coords
            disp_left   = int(l*scale + offset_x)
            disp_top    = int(t*scale + offset_y)
            disp_right  = int(r*scale + offset_x)
            disp_bottom = int(b*scale + offset_y)
            self.canvas.coords(self.rect_id, disp_left, disp_top, disp_right, disp_bottom)
            self.canvas.tag_raise(self.rect_id)

    # -------------------------------------------------------------------------
    # DRAGGING
    # -------------------------------------------------------------------------
    def on_mouse_down(self, event):
        self.active_drag = None
        if self.rect_id is None:
            return
        coords = self.canvas.coords(self.rect_id)
        if len(coords) != 4:
            return

        dleft, dtop, dright, dbottom = coords
        x, y = event.x, event.y

        # corners
        corners = [
            (dleft, dtop),       # top-left
            (dright, dtop),      # top-right
            (dleft, dbottom),    # bottom-left
            (dright, dbottom)    # bottom-right
        ]
        for i, (cx, cy) in enumerate(corners):
            if abs(x - cx) <= self.corner_hit_size and abs(y - cy) <= self.corner_hit_size:
                self.active_drag = ("corner", i)
                return

        # edges
        if (abs(y - dtop) <= self.edge_hit_size) and (dleft <= x <= dright):
            self.active_drag = ("edge", "top")
            return
        if (abs(y - dbottom) <= self.edge_hit_size) and (dleft <= x <= dright):
            self.active_drag = ("edge", "bottom")
            return
        if (abs(x - dleft) <= self.edge_hit_size) and (dtop <= y <= dbottom):
            self.active_drag = ("edge", "left")
            return
        if (abs(x - dright) <= self.edge_hit_size) and (dtop <= y <= dbottom):
            self.active_drag = ("edge", "right")
            return

    def on_mouse_drag(self, event):
        if not self.active_drag or self.rect_id is None:
            return
        coords = self.canvas.coords(self.rect_id)
        if len(coords) != 4:
            return
        dleft, dtop, dright, dbottom = coords
        x, y = event.x, event.y

        drag_type, data = self.active_drag
        if drag_type == "corner":
            idx = data
            if idx == 0:  # top-left
                dleft = min(x, dright - 2)
                dtop  = min(y, dbottom - 2)
            elif idx == 1:  # top-right
                dright = max(x, dleft + 2)
                dtop   = min(y, dbottom - 2)
            elif idx == 2:  # bottom-left
                dleft   = min(x, dright - 2)
                dbottom = max(y, dtop + 2)
            elif idx == 3:  # bottom-right
                dright  = max(x, dleft + 2)
                dbottom = max(y, dtop + 2)
        else:  # "edge"
            edge = data
            if edge == "top":
                dtop = min(y, dbottom - 2)
            elif edge == "bottom":
                dbottom = max(y, dtop + 2)
            elif edge == "left":
                dleft = min(x, dright - 2)
            elif edge == "right":
                dright = max(x, dleft + 2)

        self.canvas.coords(self.rect_id, dleft, dtop, dright, dbottom)
        self._update_raw_crop(dleft, dtop, dright, dbottom)

    def on_mouse_up(self, event):
        self.active_drag = None

    def _update_raw_crop(self, dleft, dtop, dright, dbottom):
        cwidth  = self.canvas.winfo_width()
        cheight = self.canvas.winfo_height()
        if cwidth < 1 or cheight < 1 or self.raw_width < 1 or self.raw_height < 1:
            return

        frame_aspect = self.raw_width / self.raw_height
        canvas_aspect = cwidth / cheight
        if canvas_aspect > frame_aspect:
            scale = cheight / self.raw_height
        else:
            scale = cwidth / self.raw_width

        offset_x = (cwidth - self.display_width)//2
        offset_y = (cheight - self.display_height)//2

        raw_left   = (dleft   - offset_x)/scale
        raw_top    = (dtop    - offset_y)/scale
        raw_right  = (dright  - offset_x)/scale
        raw_bottom = (dbottom - offset_y)/scale

        # clamp
        if raw_left < 0: raw_left = 0
        if raw_top < 0:  raw_top  = 0
        if raw_right > self.raw_width:
            raw_right = self.raw_width
        if raw_bottom > self.raw_height:
            raw_bottom = self.raw_height
        if raw_left >= raw_right:
            raw_right = raw_left + 2
        if raw_top >= raw_bottom:
            raw_bottom = raw_top + 2

        self.crop_coords = [raw_left, raw_top, raw_right, raw_bottom]
        self._update_crop_entry()

    # -------------------------------------------------------------------------
    # UPDATE CROP ENTRY
    # -------------------------------------------------------------------------
    def _update_crop_entry(self):
        """Show the current offsets = (left, right, top, bottom) in the crop_entry box."""
        if not self.crop_coords:
            return
        l, t, r, b = self.crop_coords

        # how many pixels from each side
        left_off   = int(round(l))
        right_off  = int(round(self.raw_width - r))
        top_off    = int(round(t))
        bottom_off = int(round(self.raw_height - b))

        # snap to even
        left_off   = snap_to_even(left_off)
        right_off  = snap_to_even(right_off)
        top_off    = snap_to_even(top_off)
        bottom_off = snap_to_even(bottom_off)

        text = f"{left_off},{right_off},{top_off},{bottom_off}"

        # replace the current text
        self.crop_entry.delete(0, tk.END)
        self.crop_entry.insert(0, text)

def main():
    args = parse_arguments()
    media_path = args.file
    if not os.path.isfile(media_path):
        print(f"Error: file not found: {media_path}")
        return

    cap = cv2.VideoCapture(media_path)
    if not cap.isOpened():
        print(f"Error: could not open {media_path}")
        return

    root = tk.Tk()
    root.title("Video Cropping Preview")

    app = CropGUI(root, cap)
    root.mainloop()
    cap.release()

if __name__ == "__main__":
    main()
