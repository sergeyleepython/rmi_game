from collections import defaultdict
import time

import Pyro4
from Pyro4.errors import CommunicationError

WAITING_FOR_QUESTION = 'WAITING_FOR_QUESTION'
WAITING_FOR_ANSWERS = 'WAIT_FOR_ANSWERS'

STARTING = 'STARTING'
IN_PROGRESS = 'IN_PROGRESS'


@Pyro4.expose
class User(object):
    username = ''
    users_dict = {}
    scoreboard = []
    correct_answer = None
    timeout_for_answer = 3
    timeout_for_question = 20
    timeout_waiting_answers = 10
    question_asked_at = None
    global_state = {'active_user': username, 'current_global_state': WAITING_FOR_QUESTION,
                    'question': 'What is going on?', 'leaderboard': defaultdict(int)}

    def __init__(self, username):
        self.username = username
        self.global_state['active_user'] = username

    def start(self, local_state=None):
        if local_state == STARTING:
            self.set_current_global_state()
        elif local_state == IN_PROGRESS:
            print('started in progress')
        active_user = self.global_state['active_user']
        print('username: {}'.format(self.username))
        print('Active user: {}'.format(active_user))
        if self.global_state['current_global_state'] == WAITING_FOR_QUESTION:
            print('Current state: {}'.format(WAITING_FOR_QUESTION))
            if active_user == self.username:
                # ACTIVE: prompt user for question and answer (30 sec)
                question = None
                correct_answer = None
                while question is None or correct_answer is None:
                    question = input("Please enter question: ")
                    self.global_state['current_global_state'] = WAITING_FOR_ANSWERS
                    self.global_state['question'] = question
                    correct_answer = input("Please enter correct answer: ")
                    try:
                        self.correct_answer = int(correct_answer)
                    except ValueError:
                        correct_answer = None
                print('Asking question {}. Answer is {}'.format(self.global_state['question'], self.correct_answer))
                self.question_asked_at = time.time()
                self.broadcast_state(self.global_state)
        elif self.global_state['current_global_state'] == WAITING_FOR_ANSWERS:
            print('Current state: {}'.format(WAITING_FOR_ANSWERS))
            if active_user != self.username:
                # PASSIVE: send answer (3 sec)
                time_started = time.time()
                answer = None
                while (self.global_state['current_global_state'] == WAITING_FOR_ANSWERS) or answer is None:
                    answer = input("Please enter answer: ")
                    try:
                        answer = int(answer)
                    except ValueError:
                        answer = None
                self.send_answer(answer)
        else:
            raise NotImplementedError

    def set_current_global_state(self):
        """ Get it from any user. If no users create state and become an active user. """
        with Pyro4.locateNS() as ns:
            for user, user_uri in ns.list(prefix="intuition.").items():
                if user.split('.')[-1] != self.username:
                    print('Extracted global state from: {}'.format(user))
                    # try:
                    with Pyro4.Proxy(user_uri) as any_user_object:
                        self.global_state = any_user_object.remote_global_state
                        break
                        # except CommunicationError:
                        #     pass

    def set_users(self):
        """ Get list of users either from NS or from stored list """
        try:
            users_dict = {}
            with Pyro4.locateNS() as ns:
                for user, user_uri in ns.list(prefix="intuition.").items():
                    users_dict[user.split('.')[-1]] = user_uri
            self.users_dict = users_dict
        except KeyError:
            # NS is unavailable but we continue working with the current users
            pass
        if not self.users_dict:
            raise ValueError("No users found!")

    def remote_set_new_state(self, new_state):
        """ PASSIVE: receive a new state from active user """
        print('Received new state: {}'.format(new_state))
        self.global_state = new_state
        self.start(IN_PROGRESS)

    def send_answer(self, answer):
        """ PASSIVE: Find active user and send him answer """
        active_username = self.global_state['active_user']
        print(active_username)
        self.set_users()
        print(self.users_dict)
        active_user_uri = self.users_dict[active_username]
        print(active_user_uri)
        with Pyro4.Proxy(active_user_uri) as active_user_object:
            print("Active user: {}".format(active_user_object))
            active_user_object.remote_receive_answer(self.username, answer)

    def define_next_active_user_by_order(self):
        """ ACTIVE: choose next after current"""
        self.set_users()
        print(self.users_dict)
        usernames_list = list(self.users_dict.keys())
        usernames_list = sorted(usernames_list)
        current_index = usernames_list.index(self.global_state['active_user'])
        if (current_index + 1) == len(usernames_list):
            next_username = usernames_list[0]
        else:
            next_username = usernames_list[current_index + 1]
        return next_username

    def broadcast_state(self, new_state):
        """ ACTIVE: broadcast to all passive users """
        # self.set_users()
        # set scoreboard
        print('Broadcasting {} for {}'.format(new_state, self.users_dict))
        for username, user_uri in self.users_dict.items():
            if username != self.username:
                with Pyro4.Proxy(user_uri) as user_object:
                    user_object.remote_set_new_state(new_state)

    def remote_receive_answer(self, username, answer):
        """ ACTIVE: receive answers and calculate scores """
        print('Received from {} - {}'.format(username, answer))
        if answer is not None and self.correct_answer is not None:
            print('Correct answer: {}'.format(self.correct_answer))
            answer_delta = abs(self.correct_answer - answer)
            self.scoreboard.append((username, answer_delta))
            print('Scoreboard: {}'.format(self.scoreboard))
        if time.time() > (self.question_asked_at + self.timeout_waiting_answers):
            self.correct_answer = None
            self.question_asked_at = None
            self.calculate_winner()
            self.global_state['active_user'] = self.define_next_active_user_by_order()
            self.global_state['current_global_state'] = WAITING_FOR_QUESTION
            self.broadcast_state(self.global_state)
            self.start(IN_PROGRESS)

    def calculate_winner(self):
        """ ACTIVE: calculate winner and update leaderboard """
        if self.scoreboard:
            winner = sorted(self.scoreboard, key=lambda tup: tup[1])[0][0]
            self.global_state['leaderboard'][winner] += 1
            self.scoreboard = []

    @Pyro4.expose
    @property
    def remote_global_state(self):
        return self.global_state


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    args = parser.parse_args()

    print('Starting ...')
    user = User(args.username)
    user.start(local_state=STARTING)
    with Pyro4.Daemon(host='10.240.19.119') as daemon:
        user_uri = daemon.register(user, user+'_id')
        with Pyro4.locateNS() as ns:
            ns.register("intuition.{}".format(args.username), user_uri)
        print("Working ...")
        daemon.requestLoop()
