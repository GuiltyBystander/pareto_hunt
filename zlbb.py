import json
import math
import os
import urllib.request

import db


def check_infinity(score, cat):
    if score[cat] == "Infinity" or score[cat] is None:
        score[cat] = math.inf


def update_community(pn):
    print('getting community scores', pn)

    url = f"https://zlbb.faendir.com/om/puzzle/{pn}/records?includeFrontier=true"
    # print(url)

    raw = urllib.request.urlopen(url).read().decode('utf-8')
    # uh, check that it's not broken?

    # delete old entries
    sql = "DELETE FROM community WHERE puzzle_name=?"
    db.con.execute(sql, [pn])

    jj = json.loads(raw)
    print(len(jj), 'community files')
    sql = """INSERT OR REPLACE INTO community
            (solution_file, gif_file, puzzle_name, category,
            mCost, mCycles, mArea, mInstructions,
            mHeight, mWidth, mBestagon, mRate,
            mAreaInfLevel, mAreaInfValue, mHeightInf, mWidthInf, mBestagonInf,
            mTrackless, mOverlap, mLoop)
            VALUES (?,?,?,?,
            ?,?,?,?,
            ?,?,?,?,
            ?,?,?,?,?,
            ?,?,?
            )"""

    data = []
    for j in jj:
        if j['solution'] is None:
            j['solution'] = j['gif']
        score = j['score']

        check_infinity(score, 'rate')
        check_infinity(score, 'areaINFLevel')
        check_infinity(score, 'areaINFValue')
        check_infinity(score, 'heightINF')
        check_infinity(score, 'widthINF')
        check_infinity(score, 'boundingHexINF')

        if score['rate'] == math.inf:
            score['loop'] = 0
        else:
            score['loop'] = 1

        datum = [j['solution'], j['gif'], pn, j['smartFormattedCategories'],
                 score['cost'], score['cycles'], score['area'], score['instructions'],
                 score['height'], score['width'], score['boundingHex'], score['rate'],
                 score['areaINFLevel'], score['areaINFValue'], score['heightINF'], score['widthINF'], score['boundingHexINF'],
                 score['trackless'], score['overlap'], score['loop']]
        data.append(datum)
    db.con.executemany(sql, data)

    # mark that we have new data
    sql = """INSERT OR REPLACE INTO community_cache
    (puzzle_name, last_check)
    VALUES (?,CURRENT_TIMESTAMP)"""
    db.con.execute(sql, [pn])
    db.con.commit()


def update_all():
    for j in puzzles.values():
        sql = "SELECT * FROM community WHERE puzzle_name=?"
        rows = db.con.execute(sql, [j['id']]).fetchall()
        print(j['id'], len(rows))

        # if len(rows) == 0:
        update_community(j['id'])


def get_puzzles():
    print('Getting puzzle list')
    url = 'https://zlbb.faendir.com/om/puzzles'
    raw = urllib.request.urlopen(url).read().decode('utf-8')
    jj = json.loads(raw)

    os.makedirs('puzzle', exist_ok=True)
    for j in jj:
        filename = os.path.join('puzzle', f'{j["id"]}.puzzle')
        if not os.path.exists(filename):
            print('Downloading puzzle', j['id'])
            puzzle_url = f'https://zlbb.faendir.com/om/puzzle/{j["id"]}/file'
            urllib.request.urlretrieve(puzzle_url, filename)

    return {j['id']: j for j in jj}


def get_remap(puzzles):
    remap = {}
    for puz in puzzles.values():
        if 'altIds' in puz:
            for alt in puz['altIds']:
                remap[alt] = puz['id']
    return remap


def get_categories():
    print('Getting tracked categories')
    url = 'https://zlbb.faendir.com/om/categories'
    raw = urllib.request.urlopen(url).read().decode('utf-8')
    jj = json.loads(raw)

    for metric in jj:
        metric['fullmetrics'] = metric['metrics'] + [m for m in metric['manifold']['metrics'] if m not in metric['metrics']]
        # todo, use '' instead?
        if metric['metrics'][0] != "O":
            metric['fullmetrics'].insert(0, '!O')
    return jj


def get_manifold_sql():
    print('Getting manifold list')
    url = 'https://zlbb.faendir.com/om/manifolds'
    raw = urllib.request.urlopen(url).read().decode('utf-8')
    jj = json.loads(raw)

    vic = {'g': 'mCost', 'c': 'mCycles', 'a': 'mArea', 'i': 'mInstructions', 'h': 'mHeight', 'w': 'mWidth', 'b': 'mBestagon', 'r': 'mRate', 'T': 'mTrackless', 'O': 'mOverlap', 'L': 'mLoop'}
    inf = {'g': 'mCost', 'c': 'mCycles', 'a': '(mAreaInfLevel, mAreaInfValue)', 'i': 'mInstructions', 'h': 'mHeightInf', 'w': 'mWidthInf', 'b': 'mBestagonInf', 'r': 'mRate', 'T': 'mTrackless', 'O': 'mOverlap', 'L': 'mLoop'}

    #        0                1                 2                 3         4           5        6                  7           8          9             10        11                12                13             14            15               16             17           18
    # SELECT puzzle_name,    'db',             'solution_name',   mCost,    mCycles,    mArea,   mInstructions,     mHeight,    mWidth,    mBestagon,    mRate,    mAreaInfLevel,    mAreaInfValue,    mHeightInf,    mWidthInf,    mBestagonInf,    mTrackless,    mOverlap,    mLoop from community
    fields = "a.puzzle_name, a.solution_file, a.solution_name, a.mcCost, a.mcCycles, a.mcArea, a.mcInstructions, a.mcHeight, a.mcWidth, a.mcBestagon, a.mcRate, a.mcAreaInfLevel, a.mcAreaInfValue, a.mcHeightInf, a.mcWidthInf, a.mcBestagonInf, a.mcTrackless, a.mcOverlap, a.mcLoop"

    sqls = []
    for manifold in jj:
        names = vic if "VICTORY" in manifold['id'] else inf
        ms = [names[_] for _ in manifold['metrics']]

        # todo, maybe only do the IFNULL checks on relevant fields
        name = ''.join(manifold["metrics"])
        extra = 'AND a.mcLoop==1' if 'r' in name else ''

        # , "{name}" as man
        # a.omsimtime, "{name}" as man,
        sql = f"""SELECT {fields}, IFNULL(a.last_check < community_cache.last_check, 0) as upToDate FROM local a
LEFT JOIN community_cache ON a.puzzle_name = community_cache.puzzle_name
WHERE a.valid {extra}
AND NOT EXISTS (
	SELECT * FROM community b
	WHERE a.puzzle_name = b.puzzle_name
"""
        # There can't be anything in `community` that is better than or equal on all metrics
        for f in ms:
            f1 = f.replace('m', 'b.m')
            f2 = f.replace('m', 'a.mc')
            order = '>' if f in ('mTrackless', 'mLoop') else '<'
            sql += f"	AND IFNULL({f1} {order}= {f2}, 1)\n"
        sql += """)
AND NOT EXISTS (
	SELECT * FROM local c
	WHERE a.puzzle_name = c.puzzle_name
"""
        # There can't be anything in `local` that is better than or equal on all metrics
        for f in ms:
            f1 = f.replace('m', 'c.mc')
            f2 = f.replace('m', 'a.mc')
            order = '>' if f in ('mTrackless', 'mLoop') else '<'
            sql += f"	AND IFNULL({f1} {order}= {f2}, 1)\n"
        sql += """	AND( 0
"""
        # and we check that at least 1 metric is strictly better,
        # otherwise it will be invalided by itself
        for f in ms:
            f1 = f.replace('m', 'c.mc')
            f2 = f.replace('m', 'a.mc')
            order = '>' if f in ('mTrackless', 'mLoop') else '<'
            sql += f"		OR IFNULL({f1} {order} {f2}, 0)\n"

        sql += """	)
)"""

        sqls.append(sql)

    return '\n\nUNION\n\n'.join(sqls) + '\n\nORDER BY a.puzzle_name, a.solution_file'


def get_puzzle_name(puz):
    if puz in puzzles:
        return puzzles[puz]['displayName']
    return puz


# todo: cache these locally?
puzzles = get_puzzles()
remap = get_remap(puzzles)
categories = get_categories()
manifold_sql = get_manifold_sql()
