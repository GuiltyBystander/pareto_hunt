# https://github.com/ianh/omsim  #thank you panic!

import datetime
import json
import math
import os
import platform
import sys
import urllib.request
from ctypes import cdll, c_void_p, c_char_p, c_double

import zlbb

# load panic's tool
if platform.system() in ('Windows'):
    lib_file = 'libverify.dll'
elif platform.system() in ('Darwin', 'Linux'):
    lib_file = 'libverify.so'
else:
    print("Huh, you're on a weird os I don't know or I broke something.  Hopefully you know how to fix this better than I do.")
    print("Find this comment in the code and fix it, or find me on discord.")
    sys.exit()

if not os.path.isdir('lib'):
    os.mkdir('lib')
lib_path = os.path.join('lib', lib_file)
# print(lib_file, lib_path)
last_modify = datetime.datetime(2000, 1, 1).astimezone()
if os.path.isfile(lib_path):
    last_modify = datetime.datetime.fromtimestamp(os.path.getmtime(lib_path)).astimezone()

# check github for newer version
try:
    url = 'https://api.github.com/repos/ianh/omsim/releases'
    raw = urllib.request.urlopen(url).read().decode('utf-8')
    jj = json.loads(raw)

    asset_file = [(datetime.datetime.fromisoformat(asset['updated_at']), asset['name'], asset['browser_download_url']) for release in jj for asset in release['assets'] if asset['name'] == lib_file]
    asset_file.sort(reverse=True)
    # print(asset_file)
    # print(last_modify, asset_file[0][1])

    if last_modify < asset_file[0][0]:
        print('Downloading new omsim')
        urllib.request.urlretrieve(asset_file[0][2], lib_path)
except Exception as e:
    print('Something went wrong downloading latest omsim. Will try to use existing version', e)


if not os.path.isfile(lib_path):
    print(f'Cannot load omsim.  Maybe try downloading it yourself and saving it to `{lib_path}`')
    print('https://github.com/ianh/omsim/releases')
    sys.exit()
# sys.exit()

lv = cdll.LoadLibrary(lib_path)


lv.verifier_create.restype = c_void_p
lv.verifier_error.restype = c_char_p
lv.verifier_evaluate_approximate_metric.restype = c_double

# I had problems in the past with it running slow on large area puzzles but it seems much better now.
# if you want to skip big area puzzles, modify this
# todo: move to settings
MAX_AREA = 100000
MAX_CYCLES = 100000
# current "worst" scores on the leaderboard for comparison are
# area:    73k   Mist of Dousing   GR
# area:    80k   Critellium
# cycles:  22k   Alchemical Slag   A


def get_metric(v, name: bytes):
    r = lv.verifier_evaluate_metric(c_void_p(v), c_char_p(name))
    if r < 0:
        return math.inf
    return r


def get_metric_approx(v, name: bytes):
    r = lv.verifier_evaluate_approximate_metric(c_void_p(v), c_char_p(name))
    if r < 0:
        return math.inf
    return r


def is_legal(v, sol):
    """overlap
    + max(0, parts of type baron - 1)
    + max(0, "parts of type glyph-disposal" - 1)
    + duplicate reagents
    + duplicate products
    + max(0, "maximum track gap^2" - 1)"""

    wheel = get_metric(v, b'parts of type baron')
    disposal = get_metric(v, b'parts of type glyph-disposal')
    reagents = not get_metric(v, b'duplicate reagents')
    products = not get_metric(v, b'duplicate products')
    track = get_metric(v, b'maximum track gap^2')
    overlap = get_metric(v, b'overlap')  # banned in production only for now

    err = lv.verifier_error(c_void_p(v))
    if err:
        # something went wrong, solution probably doesn't work
        return False
    return (wheel <= 1 and
            disposal <= 1 and
            reagents and
            products and
            track <= 1 and
            (overlap == 0 or zlbb.puzzles[sol.puzzle_name]['type'].casefold() != 'production')
            )


def get_metrics(sol):
    puzzle_file = ('puzzle/'+sol.puzzle_name+'.puzzle').encode('latin1')
    solution_file = sol.full_path.encode('latin1')
    v = lv.verifier_create(c_char_p(puzzle_file), c_char_p(solution_file))
    # lv.verifier_set_cycle_limit(c_void_p(v), c_int(MAX_CYCLES))

    if not is_legal(v, sol):
        lv.verifier_destroy(c_void_p(v))
        return False

    metrics = {
        'mpCost': get_metric(v, b'parsed cost'),
        'mpCycles': get_metric(v, b'parsed cycles'),
        'mpArea': get_metric(v, b'parsed area'),
        'mpInstructions': get_metric(v, b'parsed instructions'),

        'mcCost': get_metric(v, b'cost'),
        'mcCycles': get_metric(v, b'cycles'),
        'mcArea': get_metric(v, b'area (approximate)'),
        'mcInstructions': get_metric(v, b'instructions'),

        'mcHeight': get_metric(v, b'height'),
        'mcWidth': get_metric(v, b'width*2')/2,
        'mcBestagon': get_metric(v, b'minimum hexagon'),

        'mcTrackless': get_metric(v, b'number of track segments') == 0,
        'mcOverlap': get_metric(v, b'overlap') > 0,
    }

    err = lv.verifier_error(c_void_p(v))
    if err or metrics['mcCycles'] == math.inf:
        # something went wrong, solution probably doesn't work
        print('error before victory', err)
        lv.verifier_destroy(c_void_p(v))
        return False

    to = get_metric(v, b'throughput outputs')
    tc = get_metric(v, b'throughput cycles')

    if to <= 0 or tc <= 0 or err or to == math.inf:
        # technically, the leaderboard makes a distinction between 0 and inf, but I'm lazy
        metrics.update({
            'mcRate': math.inf,
            'mcAreaInfLevel': 2,
            'mcAreaInfValue': math.inf,
            'mcHeightInf': math.inf,
            'mcWidthInf': math.inf,
            'mcBestagonInf': math.inf,
            'mcLoop': 0,
        })
    else:
        # print('rate?', tc, to)
        areaVal = get_metric_approx(v, b'per repetition^2 area')
        if areaVal != 0.0:
            areaLev = 2
        else:
            areaVal = get_metric(v, b'per repetition area')
            if areaVal != 0:
                areaLev = 1
            else:
                areaVal = get_metric(v, b'steady state area')
                areaLev = 0

        metrics.update({
            'mcRate': math.ceil(tc/to*100)/100,
            'mcAreaInfLevel': areaLev,
            'mcAreaInfValue': areaVal,
            'mcHeightInf': get_metric(v, b'steady state height'),
            'mcWidthInf': get_metric(v, b'steady state width*2')/2,
            'mcBestagonInf': get_metric(v, b'steady state minimum hexagon'),
            'mcLoop': 1,
        })

    if zlbb.puzzles[sol.puzzle_name]['type'].casefold() == 'production':
        metrics['mcHeight'] = None
        metrics['mcWidth'] = None
        metrics['mcBestagon'] = None
        metrics['mcHeightInf'] = None
        metrics['mcWidthInf'] = None
        metrics['mcBestagonInf'] = None

    if zlbb.puzzles[sol.puzzle_name]['type'].casefold() != 'normal':
        # we don't do width/bestagon on polymer or production
        metrics['mcWidth'] = None
        metrics['mcBestagon'] = None
        metrics['mcWidthInf'] = None
        metrics['mcBestagonInf'] = None

    err = lv.verifier_error(c_void_p(v))
    if err:
        # should just be a no throughput error
        print('error after victory', err)

    lv.verifier_destroy(c_void_p(v))
    return metrics
