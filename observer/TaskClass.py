import time


class TaskClass():
    def __init__(self, wait_time: int):
        self.duration = wait_time
    def wait(self):
        time.wait(self.duration)
    def task(sefl):
        print("need to inherit")
