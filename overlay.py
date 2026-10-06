"""Debug overlay: a separate OpenCV window showing a scaled copy of the frame
with status text. Later stages draw their detections on it.

It never draws on the game itself. Place it beside the client: anything
covering the client is captured into the frames.
"""

import cv2

WINDOW_NAME = "KleenTrainer overlay"


class Overlay:
    def __init__(self, scale):
        self.scale = scale
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)

    def show(self, frame, lines):
        """Show frame scaled down, with each string in lines as status text."""
        image = cv2.resize(frame, None, fx=self.scale, fy=self.scale,
                           interpolation=cv2.INTER_AREA)
        for i, text in enumerate(lines):
            y = 24 + i * 24
            cv2.putText(image, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(image, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(WINDOW_NAME, image)

    def key(self, wait_ms):
        """Process window events; returns the key pressed in the overlay, or -1."""
        return cv2.waitKey(wait_ms)

    def is_closed(self):
        return cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1

    def close(self):
        cv2.destroyAllWindows()
