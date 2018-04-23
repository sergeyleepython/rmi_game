import time
question = None
time_started = time.time()
while (time.time() < time_started + 10) or not question:
    question = input('Enter question: ')

print(question)