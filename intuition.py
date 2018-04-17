from collections import defaultdict

import Pyro4
import time

WAITING_FOR_QUESTION = 'WAITING_FOR_QUESTION'
WAITING_FOR_ANSWERS = 'WAIT_FOR_ANSWERS'


class User(object):
    username = ''
    users_dict = {}
    scoreboard = []
    correct_answer = None
    timeout_answer = 3
    timeout_question = 20
    timeout_waiting_answers = 10
    global_state = {'active_user': username, 'current_global_state': WAITING_FOR_QUESTION,
                    'question': 'What is going on?', 'leaderboard': defaultdict(int)}
    local_state = 'idle'

    def __init__(self, username):
        self.username = username

    def get_users_from_ns(self):
        """ Get list of users from NS """
        users_dict = {}
        with Pyro4.locateNS() as ns:
            for user, user_uri in ns.list(prefix="example.intuition.").items():
                print("found user", user)
                users_dict[user] = user_uri
        return users_dict

    def set_users(self):
        """ Get list of users either from NS or from stored list """
        try:
            self.users_dict = self.get_users_from_ns()
        except KeyError:
            # NS is unavailable but we continue working with the current users
            pass
        if not self.users_dict:
            raise ValueError("No users found!")

    def define_next_active_user_by_order(self):
        """ ACTIVE: """
        self.set_users()
        usernames_list = list(self.users_dict.keys())
        usernames_list = sorted(usernames_list)
        current_index = usernames_list.index(self.global_state['active_user'])
        if current_index + 1 == len(usernames_list):
            next_username = usernames_list[0]
        else:
            next_username = usernames_list[current_index + 1]
        return next_username

    def set_current_global_state(self):
        """ Get it from any user. If no users create state and become an active user. """
        users_list = self.get_users_from_ns()
        if users_list:
            any_user = users_list[0]
            global_state = any_user.global_state
            self.global_state = global_state

    def remote_set_new_state(self, new_state):
        """ PASSIVE: receive a new state from active user """
        self.global_state = new_state
        self.start()

    def broadcast_state(self, new_state):
        """ ACTIVE: broadcast to all passive users """
        self.set_users()
        # set scoreboard

        for username, user_uri in self.users_dict.items():
            user_object = Pyro4.Proxy(user_uri)
            user_object.remote_set_new_state(new_state)

    def start(self):
        self.set_current_global_state()
        current_global_state = self.global_state['current_global_state']
        active_user = self.global_state['active_user']
        # if current_global_state == 'CHOOSING_NEXT_ACTIVE':
        #     if active_user == self.username:
        #         next_active_username = self.define_next_active_user_by_order()
        #         self.global_state['active_user'] = next_active_username
        #         self.global_state['current_global_state'] = 'WAIT_FOR_QUESTION'
        #         self.broadcast_state(self.global_state)
        if current_global_state == WAITING_FOR_QUESTION:
            if active_user == self.username:
                # ACTIVE: prompt user for question and answer (30 sec)
                time_started = time.time()
                while True:
                    if time.time() > time_started + self.timeout_question:
                        self.global_state['active_user'] = self.define_next_active_user_by_order()
                        self.broadcast_state(self.global_state)
                        break
                    else:
                        time.sleep(2)
                        self.global_state['current_global_state'] = WAITING_FOR_ANSWERS
                        self.global_state['question'] = 'my question?'
                        self.correct_answer = 42
                        self.broadcast_state(self.global_state)
                        self.question_asked_at = time.time()
                        break
        elif current_global_state == WAITING_FOR_ANSWERS:
            if active_user != self.username:
                # PASSIVE: send answer (3 sec)
                time_started = time.time()
                while True:
                    if time.time() > time_started + self.timeout_answer:
                        self.current_answer = 0
                        self.send_answer(None)
                        break
                    else:
                        # prompt input
                        time.sleep(1)
                        self.send_answer(42)
        else:
            raise NotImplementedError

    def send_answer(self, answer):
        """ PASSIVE: Find active user and send him answer """
        active_username = self.global_state['active_user']
        self.set_users()
        active_user_uri = self.users_dict[active_username]
        active_user_object = Pyro4.Proxy(active_user_uri)
        active_user_object.remote_receive_answer(self.username, answer)

    def remote_receive_answer(self, username, answer):
        """ ACTIVE: receive answers and calculate scores """
        if answer is not None:
            answer_delta = abs(self.correct_answer - answer)
            self.scoreboard.append((username, answer_delta))
            if time.time() > self.question_asked_at + self.timeout_waiting_answers:
                self.calculate_winner()
                self.correct_answer = None
                self.global_state['active_user'] = self.define_next_active_user_by_order()
                self.global_state['current_global_state'] = WAITING_FOR_QUESTION
                self.broadcast_state(self.global_state)


    def calculate_winner(self):
        """ ACTIVE: calculate winner and update leaderboard """
        if self.scoreboard:
            winner = sorted(self.scoreboard, key=lambda tup: tup[1])[0][0]
            self.global_state['leaderboard'][winner] += 1
            self.scoreboard = []
