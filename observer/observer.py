import time
from threading import Thread

from TaskClass import TaskClass


#each task sould be an obj??? 
#class object each with a running task that is called by the tread to be used



worker = TaskClass()

#cria task e usa
thread1 = Thread(target=TaskClass.task(),args=())







