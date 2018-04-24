import threading
from collections import defaultdict
import time

import Pyro4
from Pyro4.errors import CommunicationError, NamingError, ConnectionClosedError

# global states
WAITING_FOR_QUESTION = 'WAITING_FOR_QUESTION'
WAITING_FOR_ANSWERS = 'WAIT_FOR_ANSWERS'

# start options
STARTING = 'STARTING'
IN_PROGRESS = 'IN_PROGRESS'

# transition options
asking_question = 'asking_question'
sending_results = 'sending_results'
new_active_user = 'new_active_user'
TRANSITIONS = {asking_question: 'Asking a new question.', sending_results: 'Sending round results.',
               new_active_user: 'Elected new active user'}


@Pyro4.expose
class User(object):
    _username = ''
    users_dict = {}
    scoreboard = []
    answer = None
    correct_answer = None
    timeout_for_answer = 5
    timeout_waiting_answers = timeout_for_answer + 1
    global_state = {'active_user': _username,
                    'current_global_state': WAITING_FOR_QUESTION,
                    'question': None,
                    'leaderboard': defaultdict(int),
                    'scoreboard': [],
                    'correct_answer': None,
                    'round': 0,
                    'transition': None,
                    'users_dict': users_dict}
    t = None

    def __init__(self, username):
        self._username = username
        self.global_state['active_user'] = username

    def start(self, local_state=None):
        """ Main method """
        if self.t is not None:
            self.t.cancel()
            self.t = None
        if local_state == STARTING:
            print('see STARTING')
            self._set_current_global_state()
            self._set_users()
        elif local_state == IN_PROGRESS:
            print('started in progress')
        else:
            raise NotImplementedError
        active_user = self.global_state['active_user']
        print('Username: {}'.format(self._username))
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
                    if correct_answer is None:
                        correct_answer = input("Please enter correct answer: ")
                    try:
                        self.correct_answer = int(correct_answer)
                    except TypeError:
                        print('Answer is not a number. Try again.')
                        correct_answer = None
                print('Asking question {}. Answer is {}'.format(self.global_state['question'], self.correct_answer))
                # broadcast state with new question
                self._set_users()
                self._broadcast_state(self.global_state, transition=asking_question)
                time.sleep(self.timeout_waiting_answers)
                # active user will be changed
                self._read_answers()
                self._calculate_winner()
                self.global_state['active_user'] = self._define_next_active_user_by_order()
                self.global_state['question'] = None
                self.global_state['correct_answer'] = self.correct_answer
                self.global_state['current_global_state'] = WAITING_FOR_QUESTION
                self._set_users()
                self._broadcast_state(self.global_state, transition=sending_results)
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
        else:
            raise NotImplementedError
        self.t = threading.Timer(20.0, self.is_active_user_alive)
        self.t.start()

    def is_active_user_alive(self):
        print('Waiting for the active user too long. Lets check if it is alive.')
        users_dict = self.global_state['users_dict']
        uri = users_dict[self.global_state['active_user']]
        try:
            with Pyro4.Proxy(uri) as user_object:
                _ = user_object.username
            print('Yes, {} is alive. Lets keep waiting.'.format(_))
            self.t = threading.Timer(20.0, self.is_active_user_alive)
            self.t.start()
        except (CommunicationError):
            print('Yes, it is down.')
            print('Find new active user.')
            new_active = self._define_next_active_user_by_order(freeze=True)
            print('New active user is {}'.format(new_active))
            if new_active == self._username:
                print('It is me! Start broadcasting.')
                self.global_state['active_user'] = self._username
                self.global_state['current_global_state'] = WAITING_FOR_QUESTION
                self.global_state['question'] = None
                self.global_state['scoreboard'] = []
                self.global_state['correct_answer'] = None
                self.global_state['round'] += 1
                self._broadcast_state(self.global_state, transition=new_active_user)
                self.start(IN_PROGRESS)
            else:
                print('It is not me. Keep waiting.')

    def _read_answers(self):
        """ 
        Iterate over all users, except the current, and calculate the answers. 
        Edit local scoreboard and leaderboard.
        """
        print('Start reading answers from remote objects ...')
        users_objects = self._get_other_users_proxies()
        for user_object in users_objects:
            user_answer = user_object.get_answer()
            if user_answer is not None:
                answer_delta = abs(self.correct_answer - user_answer)
                self.scoreboard.append((user_object.username, answer_delta))
                print('Scoreboard: {}'.format(self.scoreboard))
                user_object.set_message('The answer is received.')
            else:
                user_object.set_message('Sorry. Time is up! You answer will not be counted.')
            # reset answer attr for all users
            user_object.set_answer(None)
        print('Finished reading answers from remote objects ...')

    @Pyro4.oneway
    def set_message(self, message):
        print(message)

    def _get_other_users_proxies(self):
        """ Helper function. Returns all the user objects except the current one. """
        try:
            with Pyro4.locateNS() as ns:
                users_uri = [user_uri for username, user_uri in ns.list(prefix="intuition.").items() if
                             username != 'intuition.{}'.format(self._username)]
                ns._pyroRelease()
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
                except (CommunicationError):
                    pass

    def _set_users(self):
        """ Set local users dict from NS or from stored list """
        try:
            users_dict = {}
            with Pyro4.locateNS() as ns:
                for user, user_uri in ns.list(prefix="intuition.").items():
                    users_dict[user.split('.')[-1]] = user_uri
            self.users_dict = self.global_state['users_dict'] = users_dict
        except KeyError:
            # NS is unavailable but we continue working with the current users
            pass
            # if not self.users_dict:
            #     raise ValueError("No users found!")

    @Pyro4.oneway
    def set_answer(self, answer):
        """ PASSIVE: set answer """
        self.answer = answer

    def get_answer(self):
        """ PASSIVE: get answer """
        return self.answer

    def _define_next_active_user_by_order(self, freeze=None):
        """ ACTIVE: choose next after current. freeze - don't update users_dict from NS."""
        print('Start defining new active user ...')
        if freeze is None:
            self._set_users()
        users_dict = self.global_state['users_dict']
        usernames_list = list(users_dict.keys())
        usernames_list = sorted(usernames_list)
        if self.global_state['active_user'] in usernames_list:
            current_index = usernames_list.index(self.global_state['active_user'])
            if (current_index + 1) == len(usernames_list):
                next_username = usernames_list[0]
            else:
                next_username = usernames_list[current_index + 1]
        else:
            next_username = usernames_list[0]
        return next_username

    def _broadcast_state(self, new_state, transition=None):
        """ ACTIVE: broadcast to all passive users and trigger new cycle """
        self.global_state['transition'] = transition
        # set scoreboard
        users = self._get_other_users_proxies()
        print('Broadcasting {} for {}'.format(new_state, users))
        for user_object in users:
            try:
                user_object.remote_set_new_state(new_state)
            except CommunicationError:
                pass

    @Pyro4.oneway
    def remote_set_new_state(self, new_state):
        """ PASSIVE: receive a new state from active user """
        transition = new_state['transition']
        new_state['transition'] = None
        print('Transition: {}'.format(TRANSITIONS[transition]))
        self.global_state = new_state
        if transition == asking_question:
            print('Round {}'.format(self.global_state['round']))
            print('Question: {}'.format(self.global_state['question']))
        elif transition == sending_results:
            print('Correct Answer is {}'.format(self.global_state['correct_answer']))
            print('Scoreboard: {}'.format(self.global_state['scoreboard']))
            self.global_state['scoreboard'] = []
            print('Leaderboard: {}'.format(self.global_state['leaderboard']))
            self.global_state['leaderboard'] = defaultdict(int)
        elif transition == new_active_user:
            print('Round {}'.format(self.global_state['round']))
        else:
            raise NotImplementedError
        self.start(IN_PROGRESS)

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
    username = args.username
    pyroname = "intuition.{}".format(username)
    with Pyro4.locateNS() as ns:
        pyronames = list(ns.list(prefix="intuition.").keys())
        if pyroname in pyronames:
            print('This name is already in use. Choose another one and retry.')
            exit()

    print('Starting ...')
    user = User(username)
    user_thread = threading.Thread(target=user.start, args=['STARTING'])
    user_thread.start()
    with Pyro4.Daemon(host='0.0.0.0') as daemon:
        user_uri = daemon.register(user, username + '_id')
        # print(user_uri)
        with Pyro4.locateNS() as ns:
            ns.register(pyroname, user_uri)
            daemon.requestLoop()
