import argparse
import getpass
import itertools
import os
import re
import time
import urllib.parse
import warnings

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait

all_sports = [
    'archery', 'artistic-swimming', 'badminton', 'baseball', 'basketball',
    'basketball-3x3', 'beach-volleyball', 'boxing', 'canoe-kayak', 'cycling',
    'diving', 'equestrian', 'fencing', 'field-hockey', 'golf', 'gymnastics',
    'handball', 'judo', 'karate', 'modern-pentathlon', 'rhythmic-gymnastics',
    'rowing', 'rugby', 'sailing', 'shooting', 'skateboarding', 'soccer',
    'softball', 'sport-climbing', 'surfing', 'swimming', 'table-tennis',
    'taekwondo', 'tennis', 'track-field', 'trampoline', 'triathlon',
    'volleyball', 'water-polo', 'weightlifting', 'wrestling'
]

all_cable_providers = [
    'comcast_sso', 'dtv', 'dish', 'att', 'verizon', 'cox', 'spectrum',
    'cablevision', 'suddenlink', 'mediacom', 'auth_cableone_net', 'wow', 'rcn',
    'auth_armstrongmywire_com', 'frontier_auth-gateway_net', 'aafexch'
]

resolution_constants = {
    '1080p': '6596000',
    '720p': '4596000'
}

parser = argparse.ArgumentParser()
parser.add_argument('-u', '--username', nargs='?', default=None)
parser.add_argument('-p', '--password', nargs='?', default=None)
parser.add_argument('-c', '--cable-provider', nargs='?', default=None, choices=all_cable_providers)
parser.add_argument('-s', '--sport', required=True, choices=all_sports)
parser.add_argument('-r', '--resolution', choices=[*resolution_constants.keys(), 'all'], default='1080p')
parser.add_argument('-d', '--delay', nargs='?', default=10, help='Delay between clicking subsequent vod links')
args = parser.parse_args()


if args.resolution == '1080p' and args.cable_provider is None:
    warnings.warn('1080p streams will not be available without cable provider log-in')

base_url = 'https://www.nbcolympics.com/replays/sport/'
m3u8_regex = r'https://sprt.*?VIDEO_\d_\d+?_vod\.m3u8'

ad_domains = ['fwmrm.net']

caps = DesiredCapabilities.CHROME
caps['goog:loggingPrefs'] = {'performance': 'ALL'}

chrome_option = webdriver.ChromeOptions()
chrome_option.add_argument('--remote-debugging-port=9222')


driver = webdriver.Chrome(
    desired_capabilities=caps,
    options=chrome_option,
    executable_path=os.path.abspath('chromedriver')
)

cable_username = args.username or getpass.getpass(prompt=f'{args.cable_provider} username: ')
cable_password = args.password or getpass.getpass(prompt=f'{args.cable_provider} password: ')

driver.get(f'{base_url}{args.sport}')

WebDriverWait(driver, 10).until(
    expected_conditions.element_to_be_clickable((By.CSS_SELECTOR, '.post-card__link')),
)

driver.find_element_by_class_name('cookie-content__button').click()

loaded_all = False
while not loaded_all:
    try:
        elem = driver.find_element_by_class_name('cta-button__wrapper')
    except NoSuchElementException:
        loaded_all = True
    else:
        elem.click()
    time.sleep(1)


vod_links = [
    elem.get_attribute('href')
    for elem in driver.find_elements_by_class_name('post-card__link')
]


def process_vod(link):
    def do_login():
        def get_login_field(identifiers):
            html_types = ['input', 'button']
            field_types = ['id', 'type']

            for h, f, i in itertools.product(html_types, field_types, identifiers):
                try:
                    # providers have different but very similar combos of ids
                    return driver.find_element_by_xpath(f"//{h}[@{f}='{i}']")
                except NoSuchElementException:
                    pass
            else:
                raise NoSuchElementException(i)

        # assume timeout means direct to login page
        try:
            WebDriverWait(driver, 10).until(
                expected_conditions.element_to_be_clickable((By.CSS_SELECTOR, '.temp-pass-mobile-login')),
            )
        except TimeoutException:
            pass

        try:
            login_button = driver.find_element_by_class_name('temp-pass-mobile-login')
        except NoSuchElementException:
            pass
        else:
            login_button.click()

        try:
            WebDriverWait(driver, 10).until(
                expected_conditions.element_to_be_clickable((By.ID, 'access-enabler-provider-search'))
            )
        except TimeoutException:
            # already logged in
            pass
        else:
            providers = {
                re.sub(r'.*/assets/page/mvpds/picker/(.*)\.png', r'\1', p.get_attribute('src')).lower(): p
                for p in driver.find_elements_by_class_name('mvpd-logo')
            }

            try:
                providers[args.cable_provider.lower()].click()
            except KeyError:
                raise ValueError(f'Provider {args.cable_provider} not available')

            WebDriverWait(driver, 10).until(
                expected_conditions.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
            )

            username_field = get_login_field(['username', 'user'])
            password_field = get_login_field(['password', 'pass', 'passwd'])
            submit_button = get_login_field(['submit', 'sign_in'])

            username_field.send_keys(cable_username)
            time.sleep(1)
            password_field.send_keys(cable_password)
            time.sleep(1)
            submit_button.click()

    m3u8_links = {}
    driver.get(link)

    login_success = False
    login_attempts = 0
    login_attempt_limit = 5

    if args.cable_provider is not None:
        while not login_success:
            do_login()
            try:
                WebDriverWait(driver, 10).until(
                    expected_conditions.element_to_be_clickable((By.CSS_SELECTOR, '.click-to-play-button'))
                )
            except TimeoutException:
                login_attempts += 1

                if login_attempts >= login_attempt_limit:
                    raise RuntimeError(f'{login_attempts} login attempts have failed, aborting.')

                time.sleep(1)
            else:
                login_success = True

    play_button = driver.find_element_by_class_name('click-to-play-button')
    play_button.click()

    while args.resolution not in m3u8_links or (args.resolution == 'all' and len(m3u8_links) < 2):
        perf = driver.get_log('performance')

        for item in perf:
            if 'Network' in item['message'] and 'm3u8' in item['message']:
                links = re.findall(m3u8_regex, item['message'])

                for link in links:
                    if all(ad_domain not in link for ad_domain in ad_domains):
                        link = urllib.parse.unquote(link)

                        if args.resolution == 'all':
                            for res, constant in resolution_constants.items():
                                if constant in link:
                                    m3u8_links[res] = link
                        else:
                            if resolution_constants[args.resolution] in link:
                                m3u8_links[args.resolution] = link
        time.sleep(1)

    title = driver.find_element_by_class_name('side-bar-content-info-title').text.replace(':', ' -')

    for res, link in m3u8_links.items():
        print(f'youtube-dl -f best "{link}" --hls-prefer-native -o "{title} [{res}].mp4"')

    time.sleep(args.delay)


for v in vod_links:
    process_vod(v)

driver.quit()
