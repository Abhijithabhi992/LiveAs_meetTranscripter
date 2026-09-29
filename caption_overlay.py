import tkinter as tk
import queue


class CaptionOverlay:

    def __init__(self):

        # ====================================================
        # Create Tkinter window
        # ====================================================

        self.root = tk.Tk()

        self.root.title(
            "Meet Live Captions"
        )

        # ----------------------------------------------------
        # Window size
        # ----------------------------------------------------

        self.width = 1000
        self.height = 150

        # ----------------------------------------------------
        # Remove Windows title bar
        # ----------------------------------------------------

        self.root.overrideredirect(True)

        # ----------------------------------------------------
        # Always stay above Google Meet
        # ----------------------------------------------------

        self.root.attributes(
            "-topmost",
            True
        )

        # ----------------------------------------------------
        # Transparency
        # ----------------------------------------------------

        self.root.attributes(
            "-alpha",
            0.92
        )

        self.root.configure(
            bg="#111111"
        )

        # ====================================================
        # Main frame
        # ====================================================

        self.frame = tk.Frame(
            self.root,
            bg="#111111"
        )

        self.frame.pack(
            fill="both",
            expand=True,
            padx=20,
            pady=15
        )

        # ====================================================
        # Caption label
        # ====================================================

        self.caption = tk.Label(
            self.frame,
            text="Waiting for meeting...",
            bg="#111111",
            fg="white",
            font=(
                "Segoe UI",
                20,
                "bold"
            ),
            justify="center",
            anchor="center",
            wraplength=940,
        )

        self.caption.pack(
            fill="both",
            expand=True
        )

        # ====================================================
        # Thread-safe queue
        # ====================================================

        self.queue = queue.Queue()

        # ====================================================
        # Drag support
        # ====================================================

        self.drag_x = 0
        self.drag_y = 0

        self.frame.bind(
            "<ButtonPress-1>",
            self.start_drag
        )

        self.frame.bind(
            "<B1-Motion>",
            self.drag_window
        )

        self.caption.bind(
            "<ButtonPress-1>",
            self.start_drag
        )

        self.caption.bind(
            "<B1-Motion>",
            self.drag_window
        )

        # ====================================================
        # Double click = close overlay
        # ====================================================

        self.frame.bind(
            "<Double-Button-1>",
            lambda event: self.close()
        )

        self.caption.bind(
            "<Double-Button-1>",
            lambda event: self.close()
        )

        # ====================================================
        # Right click = close
        # ====================================================

        self.frame.bind(
            "<Button-3>",
            lambda event: self.close()
        )

        self.caption.bind(
            "<Button-3>",
            lambda event: self.close()
        )

        # ====================================================
        # Keyboard shortcut
        # ====================================================

        self.root.bind(
            "<Control-Shift-C>",
            self.toggle_visibility
        )

        # ====================================================
        # Position window
        # ====================================================

        self.position_window()

        # ====================================================
        # Start queue processor
        # ====================================================

        self.root.after(
            50,
            self.process_queue
        )

        self.visible = True

    # ========================================================
    # Position
    # ========================================================

    def position_window(self):

        screen_width = (
            self.root.winfo_screenwidth()
        )

        screen_height = (
            self.root.winfo_screenheight()
        )

        x = (
            screen_width - self.width
        ) // 2

        # Bottom center
        y = (
            screen_height
            - self.height
            - 110
        )

        self.root.geometry(
            f"{self.width}x{self.height}"
            f"+{x}+{y}"
        )

    # ========================================================
    # Drag
    # ========================================================

    def start_drag(self, event):

        self.drag_x = event.x
        self.drag_y = event.y

    def drag_window(self, event):

        x = (
            self.root.winfo_x()
            + event.x
            - self.drag_x
        )

        y = (
            self.root.winfo_y()
            + event.y
            - self.drag_y
        )

        self.root.geometry(
            f"+{x}+{y}"
        )

    # ========================================================
    # Update caption
    #
    # SAFE TO CALL FROM OTHER THREADS.
    # ========================================================

    def update_caption(
        self,
        speaker,
        text
    ):

        if not text:
            return

        self.queue.put(
            (
                "caption",
                speaker,
                text,
            )
        )

    # ========================================================
    # Update status
    #
    # SAFE TO CALL FROM OTHER THREADS.
    # ========================================================

    def update_status(
        self,
        text
    ):
        self.queue.put(
            (
                "status",
                "",
                text,
            )
        )


    def close_after(
        self,
        milliseconds=1500
    ):
        """
        Request the Tkinter window to close after a short delay.

        This method is safe to call from the background
        transcription thread.
        """

        self.queue.put(
            (
                "close",
                "",
                milliseconds,
            )
        )

    # ========================================================
    # Process queue
    #
    # This runs ONLY on the Tkinter main thread.
    # ========================================================

    def process_queue(self):

        try:

            while True:

                item = (
                    self.queue.get_nowait()
                )

                item_type = item[0]

                if item_type == "caption":

                    speaker = item[1]
                    text = item[2]

                    self.caption.config(
                        text=f"{speaker}: {text}"
                    )

                elif item_type == "status":

                    text = item[2]

                    self.caption.config(
                        text=text
                    )


                elif item_type == "close":

                    milliseconds = item[2]

                    self.root.after(
                        milliseconds,
                        self.close
                    )

        except queue.Empty:

            pass

        self.root.after(
            50,
            self.process_queue
        )

    # ========================================================
    # Show / hide
    # ========================================================

    def toggle_visibility(
        self,
        event=None
    ):

        if self.visible:

            self.root.withdraw()

            self.visible = False

        else:

            self.root.deiconify()

            self.root.attributes(
                "-topmost",
                True
            )

            self.visible = True

    # ========================================================
    # Close
    # ========================================================

    def close(self):

        self.root.destroy()

    # ========================================================
    # Run
    # ========================================================

    def run(self):

        self.root.mainloop()