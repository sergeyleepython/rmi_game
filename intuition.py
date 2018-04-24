import threading
from collections import defaultdict
import time

import Pyro4
from Pyro4.errors import CommunicationError, NamingError, ConnectionClosedError

WAITING_FOR_QUESTION = 'WAITING_FOR_QUESTION'
WAITING_FOR_ANSWERS = 'WAIT_FOR_ANSWERS'

STARTING = 'STARTING'
IN_PROGRESS = 'IN_PROGRESS'


@Pyro4.expose
class User(object):
    _username = ''
    users_dict = {}
    scoreboard = []
    answer = None
    correct_answer = None
    timeout_for_answer = 5
    timeout_waiting_answers = timeout_for_answer + 1
    global_state = {'active_user': _username, 'current_global_state': WAITING_FOR_QUESTION,
                    'question': None, 'leaderboard': defaultdict(int), 'scoreboard': [],
                    'correct_answer': None, 'round': 0}

    def __init__(self, username):
        self._username = username
        self.global_state['active_user'] = username

    def start(self, local_state=None):
        """ Main method """
        if local_state == STARTING:
            print('see STARTING')
            self._set_current_global_state()
        elif local_state == IN_PROGRESS:
            print('started in progress')
        active_user = self.global_state['active_user']
        print('username: {}'.format(self._username))
        print('Active user: {}'.format(active_user))
        if self.global_state['current_global_state'] == WAITING_FOR_QUESTION:
            print('Global state: {}'.format(WAITING_FOR_QUESTION))
            if active_user == self._username:
                # ACTIVE: ask question, wait and gather answers
                self.global_state['round'] += 1
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
                        print('Answer is not a number. Try again.')
                        correct_answer = None
                print('Asking question {}. Answer is {}'.format(self.global_state['question'], self.correct_answer))
                # broadcast state with new question
                self._set_users()
                self._broadcast_state(self.global_state)
                time.sleep(self.timeout_waiting_answers)
                # active user will be changed
                self._read_answers()
                self._calculate_winner()
                self.global_state['active_user'] = self._define_next_active_user_by_order()
                self.global_state['question'] = None
                self.global_state['current_global_state'] = WAITING_FOR_QUESTION
                self._broadcast_state(self.global_state)
                self.correct_answer = None
                self.start(IN_PROGRESS)
            else:
                print('You are waiting for a question from another user. Just wait!')
        elif self.global_state['current_global_state'] == WAITING_FOR_ANSWERS:
            print('Global state: {}'.format(WAITING_FOR_ANSWERS))
            if active_user != self._username:
                # PASSIVE: set answer
                answer = None
                while (self.global_state['current_global_state'] == WAITING_FOR_ANSWERS) or answer is None:
                    answer = input("Please enter answer ({} sec.): ".format(self.timeout_for_answer))
                    try:
                        self.answer = int(answer)
                        print('Thank you for your answer!')
                        break
                    except ValueError:
                        print('Answer should be a number!')
                        self.answer = None

                        # if self.global_state['current_global_state'] == WAITING_FOR_QUESTION:
                        #     print('But you did not get to answer. Time is up.')
                        # self.start(IN_PROGRESS)
        else:
            raise NotImplementedError

    def _read_answers(self):
        """ 
        Iterate over all users, except the current, and calculate the answers. 
        Edit local scoreboard and leaderboard.
        """
        users_objects = self._get_other_users_proxies()
        for user_object in users_objects:
            user_answer = user_object.get_answer()
            if user_answer is not None:
                print('Correct answer: {}'.format(self.correct_answer))
                answer_delta = abs(self.correct_answer - user_answer)
                self.scoreboard.append((user_object.username, answer_delta))
                print('Scoreboard: {}'.format(self.scoreboard))
                user_object.set_message('The answer is received.')
            else:
                user_object.set_message('Sorry. Time is up! You answer will not be counted.')
            # reset answer attr for all users
            user_object.set_answer(None)

    @Pyro4.oneway
    def set_message(self, message):
        print(message)

    def _get_other_users_proxies(self):
        """ Helper function. Returns all the user objects except the current one. """

        try:
            with Pyro4.locateNS() as ns:
                users_uri = [user_uri for username, user_uri in ns.list(prefix="intuition.").items() if
                             username != 'intuition.{}'.format(self._username)]
                # ns._pyroRelease()  # todo: does it work ????
        except NamingError:
            print('Empty NS!!!!')
            users_uri = [user_uri for username, user_uri in self.users_dict if
                         username != 'intuition.{}'.format(self._username)]
        users_objects = []
        for uri in users_uri:
            try:
                with Pyro4.Proxy(uri) as user_object:
                    users_objects.append(user_object)
            except NamingError:
                print(uri + ' is not found!')
                pass
        return users_objects

    def _set_current_global_state(self):
        """ Helper function. Get it from any user. If no users create state and become an active user. """
        proxies = self._get_other_users_proxies()
        if proxies:
            for proxy in proxies:
                print('Try to set {} global setting.'.format(proxy))
                try:
                    self.global_state = proxy.remote_global_state()
                    print('Set {} global setting.'.format(proxy))
                    break
                except (NamingError, CommunicationError):
                    raise

    def _set_users(self):
        """ Set local users dict from NS or from stored list """
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

    @Pyro4.oneway
    def remote_set_new_state(self, new_state):
        """ PASSIVE: receive a new state from active user """
        print('Received new state: {}'.format(new_state))
        self.global_state = new_state
        if self.global_state['scoreboard']:
            print(self.global_state['scoreboard'])
            self.global_state['scoreboard'] = []
        self.start(IN_PROGRESS)

    @Pyro4.oneway
    def set_answer(self, answer):
        """ PASSIVE: set answer """
        self.answer = answer

    def get_answer(self):
        """ PASSIVE: get answer """
        return self.answer

    def _define_next_active_user_by_order(self):
        """ ACTIVE: choose next after current"""
        self._set_users()
        print(self.users_dict)
        usernames_list = list(self.users_dict.keys())
        usernames_list = sorted(usernames_list)
        current_index = usernames_list.index(self.global_state['active_user'])
        if (current_index + 1) == len(usernames_list):
            next_username = usernames_list[0]
        else:
            next_username = usernames_list[current_index + 1]
        return next_username

    def _broadcast_state(self, new_state):
        """ ACTIVE: broadcast to all passive users and trigger new cycle """
        # set scoreboard
        users = self._get_other_users_proxies()
        print('Broadcasting {} for {}'.format(new_state, users))
        for user_object in users:
            user_object.remote_set_new_state(new_state)

    def _calculate_winner(self):
        """ ACTIVE: calculate winner and update leaderboard """
        if self.scoreboard:
            winner = sorted(self.scoreboard, key=lambda tup: tup[1])[0][0]
            leaderboard = self.global_state['leaderboard']
            leaderboard[winner] = leaderboard.get(winner, 0) + 1
            self.global_state['scoreboard'] = self.scoreboard
            self.scoreboard = []

    def remote_global_state(self):
        return self.global_state

    @property
    def username(self):
        return self._username


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    args = parser.parse_args()

    print('Starting ...')
    user = User(args.username)
    user_thread = threading.Thread(target=user.start, args=['STARTING'])
    user_thread.start()
    with Pyro4.Daemon() as daemon:
        user_uri = daemon.register(user, args.username + '_id')
        print(user_uri)
        with Pyro4.locateNS() as ns:
            ns.register("intuition.{}".format(args.username), user_uri)
            daemon.requestLoop()
