import signal
import requests
import re
import pickle
import os.path
import matplotlib.pyplot as plt
import matplotlib.dates
import datetime
from pyquery import PyQuery as pq
from termcolor import colored
from typing import Dict, Tuple

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0',
    'Content-Type': 'text/html; charset=utf-8'
}

DATETIME_FMT = "%Y-%m-%dT%H:%M:%S%z"
SINGLE_EPISODE_REGEX = re.compile('(\d+)-(?:й|го)')
EPISODES_COUNT_REGEX = re.compile('(\d+) эпизод')
CACHE_FN = 'animeinfo.cache'

def parse_duration(s: str):
    total_duration = 0
    tokens = s.split(' ')
    for i, token in enumerate(tokens):
        if token.isdigit():
            if i + 1 >= len(tokens):
                raise Exception(f'Failed to parse duration (index out of range): {s}')
            
            if tokens[i + 1].find('час') != -1:
                total_duration += 60 * int(token)
            elif tokens[i + 1].find('мин') != -1:
                total_duration += int(token)
            else:
                raise Exception(f'Failed to parse duration (unknown duration signature {tokens[i + 1]}): {s}')
    return total_duration

def parse_episodes(s: str):
    if s.find('/') == -1:
        return int(s)
    total_episodes = s.split('/')
    if total_episodes[-1].find('?') != -1:
        return int(total_episodes[0])
    return int(total_episodes[-1])

def parse_timestamp(s: str):
    return datetime.datetime.strptime(s, DATETIME_FMT)

INFO_CACHE = {}

def get_duration_info(href):
    if href not in INFO_CACHE:
        print(colored(f'Making a request {href}...', 'blue'))
        r = requests.get(href, headers=headers)
        w = pq(r.content)
        
        ep = w('.key:contains("Эпизоды:")').eq(0)
        if len(ep) != 0:
            episodes = parse_episodes(ep.next().text())
        else:
            episodes = 1
        duration = parse_duration(w('.key:contains("Длительность эпизода:")').eq(0).next().text())
        
        INFO_CACHE[href] = (episodes, duration)
    return INFO_CACHE[href]

class HistoryItem:
    def __init__(self, title: str, href: str):
        self.title = title
        self.href = href
        self.clear()
    
    def clear(self):
        self.timestamps: Tuple[datetime.datetime, int] = []
        self.current_episode = 0
        self.total_views = 0
    
    def edit(self, action: str, date: datetime.datetime.date):
        if action.find('удалено из списка') != -1:
            self.clear()
            return (-1,)
        
        if action.find('просмотр') == -1:
            return (0,)
        
        # Example: просмотрено 5 эпизодов
        ep_count_match = EPISODES_COUNT_REGEX.findall(action)
        if len(ep_count_match) > 0:
            ep_count = int(ep_count_match[0].split(' ')[0])
            self.timestamps.append((date, ep_count))
            self.current_episode += ep_count
            return (1, ep_count)
        
        ep_match = SINGLE_EPISODE_REGEX.findall(action)
        if len(ep_match) > 0:
            if action.find(' по ') == -1:
                # Example: просмотрены 1-й, 2-й и 3-й эпизоды
                ep_count = len(ep_match)
            else:
                # Example: просмотрены с 5-го по 10-й эпизоды
                if len(ep_match) != 2:
                    raise Exception(f'Invalid action (episode range): {action}')
                ep_count = int(ep_match[1]) - int(ep_match[0]) + 1
            self.timestamps.append((date, ep_count))
            self.current_episode += ep_count
            return (1, ep_count)
        
        total_episodes, _ = get_duration_info(self.href)
        if total_episodes == 0:
            raise Exception(f'Trying to complete on-going anime')
        ep_count = total_episodes - self.current_episode
        self.timestamps.append((date, ep_count))
        self.current_episode = 0
        self.total_views += 1
        return (2, ep_count, self.total_views)

class History:
    def __init__(self):
        self.items: Dict[str, HistoryItem] = {}
    
    def process(self, history_line: pq, verbose: bool = True):
        href = history_line('a.db-entry').eq(0).attr('href')
        if href is None:
            return
        
        title = history_line('.name-en').eq(0).text()
        
        if title not in self.items:
            self.items[title] = HistoryItem(title, href)
        
        action = history_line.children('span').clone().children(':not(b)').remove().end().text()
        timestamp = history_line('time.date').eq(0).attr('datetime')
        date = parse_timestamp(timestamp)
        
        result = self.items[title].edit(action, date)
        if verbose:
            if result[0] == -1:
                action_desc = colored('удалено из списка', 'red')
            elif result[0] == 0:
                action_desc = colored('пропущено', 'dark_grey')
            elif result[0] == 1:
                action_desc = colored(f'просмотрено {result[1]} эпизодов', 'yellow')
            else:
                action_desc = colored(f'просмотрено {result[1]} эпизодов (завершен {result[2]}-й просмотр)', 'green')
            
            print(f'{colored(date, "cyan")}: {title} - {action_desc} ({colored(action, "grey")})')
    
    def show(self, span: int, step: int):
        result = {}
        for title, item in self.items.items():
            if len(item.timestamps) == 0:
                continue
            _, episode_duration = get_duration_info(item.href)
            for dt, ep_count in item.timestamps:
                date = dt.date()
                if date in result:
                    result[date] += ep_count * episode_duration
                else:
                    result[date] = ep_count * episode_duration
        
        dates = list(result.keys())
        episodes = list(result.values())
        
        kv = list(zip(*sorted(zip(dates, episodes), key=lambda t: t[0])))
        dates, episodes = kv[0], kv[1]
        
        step_delta = datetime.timedelta(days=step)
        span_delta = datetime.timedelta(days=span)
        
        current_date = dates[-1] + step_delta
        right = len(dates) - 1 
        left = right
        keys = [current_date]
        vals = [0]
        while left >= 0:
            current_date -= step_delta
            keys.append(current_date)
            vals.append(vals[-1])
            while right >= 0 and dates[right] > current_date:
                vals[-1] -= episodes[right]
                right -= 1
            while left >= 0 and current_date - dates[left] < span_delta:
                vals[-1] += episodes[left]
                left -= 1
        
        fig = plt.figure()
        ax = fig.add_subplot(111)
        ax.plot_date(matplotlib.dates.date2num(keys[:0:-1]), vals[:0:-1], 'g-')
        ax.fill_between(matplotlib.dates.date2num(keys[:0:-1]), vals[:0:-1], color="g", alpha=0.2)
        
        plt.show()
        

def dump_animeinfo():
    print(colored("Dumping animeinfo cache into file...", 'magenta'), end=" ")
    with open(CACHE_FN, 'wb') as f:
        pickle.dump(INFO_CACHE, f)
    print(colored("Done", 'magenta'))

def signal_handle(signum, frame):
    dump_animeinfo()
    exit(0)

fn = 'page.html' # input('Enter .html filename: ')
with open(fn, 'r', encoding='utf-8') as f:
    w = pq(f.read())

if os.path.isfile(CACHE_FN):
    print(colored("Loading animeinfo cache from file...", 'magenta'), end=" ")
    with open(CACHE_FN, 'rb') as f:
        INFO_CACHE = pickle.load(f)
    print(colored("Done", 'magenta'))
        
signal.signal(signal.SIGINT, signal_handle)
signal.signal(signal.SIGTERM, signal_handle)

history = History()

for history_line in list(w('.b-user_history-line:has(span)').items())[::-1]:
    history.process(history_line)

history.show(span=49, step=14)
dump_animeinfo()