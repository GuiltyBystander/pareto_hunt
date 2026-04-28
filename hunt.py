#!/usr/bin/env python

import math
import os
import platform
import shutil
import sys
import time
from solution import Solution
import db
import omsim
import zlbb

# if you want to track folders besides the standard directory, add to the list below
root_folders = []

# if on windows, auto find my documents and save folder
pp = []
if platform.system() == 'Windows':
    pp.append(os.path.expanduser(r"~\Documents\My Games\Opus Magnum"))
elif platform.system() == 'Darwin':
    pp.append(os.path.expanduser(r"~/Library/Application Support/Opus Magnum"))
elif platform.system() == 'Linux':
    pp.append(os.path.expanduser(r"~/.local/share/Opus Magnum"))
    pp.append(os.path.expanduser(r"~/data/Opus Magnum"))

for p in pp:  # yes, I'm great at picking variable names
    if os.path.isdir(p):
        for folder in os.listdir(p):
            full = os.path.join(p, folder)
            if folder.isnumeric() and os.path.isdir(full):
                root_folders.append(full)

print(root_folders)


def scan_local():
    print('Scanning local files')

    # files on disk
    local_files = dict()
    for folder in root_folders:
        for f in os.listdir(folder):
            if f.endswith('.solution'):
                full_path = os.path.join(folder, f)
                local_files[full_path] = os.path.getmtime(full_path)
    lfs = set(local_files.keys())

    # files in database
    db_files = dict()
    sql = "SELECT solution_file, strftime('%s',last_check)*1 as t FROM local"
    for f, ts in db.con.execute(sql).fetchall():
        db_files[f] = ts
    dfs = set(db_files.keys())

    # things in the database, but not on disk
    to_delete = dfs.difference(lfs)

    # things where local file is newer than database (or not in db)
    to_check = set(file for file, ts in local_files.items() if ts > db_files.get(file, 0))

    # todo: cache the puzzle type of each file to save read times
    solutions = set(Solution(f) for f in to_check)
    solutions = set(s for s in solutions if s.puzzle_name + '.puzzle' in tracked_puzzles)

    news = set()
    updates = set()
    oversized = set()
    for s in solutions:
        f = s.full_path
        if s.scores == 4 and s.area > omsim.MAX_AREA:
            oversized.add(f)
        elif f in dfs:
            updates.add(f)
        else:
            news.add(f)

    return {
        'local': local_files,
        'db': db_files,

        'new': news,
        'update': updates,
        'delete': to_delete,
        'oversized': oversized
    }


def process_solutions():
    local_stats = scan_local()

    # first delete all entries in db, but no longer on disk
    sql = 'DELETE FROM local WHERE solution_file = ?'
    for f in local_stats['delete']:
        db.con.execute(sql, [f])
        print(f)
    db.con.commit()

    # start processing new files and those which are updated since last scan
    for f in local_stats['new'] | local_stats['update']:
        sol = Solution(f)
        if sol.scores == 4 and sol.area > omsim.MAX_AREA:
            continue

        print(f)
        print(sol)

        start = time.time()
        metrics = omsim.get_metrics(sol)
        dt = time.time() - start
        data = [f, sol.solution_name, sol.puzzle_name, bool(metrics), dt]
        if metrics:
            sql = """INSERT OR REPLACE INTO local
                    (solution_file, last_check, solution_name, puzzle_name, valid, omsimtime,
                    mpCost, mpCycles, mpArea, mpInstructions,
                    mcCost, mcCycles, mcArea, mcInstructions,
                    mcHeight, mcWidth, mcBestagon, mcRate,
                    mcAreaInfLevel, mcAreaInfValue, mcHeightInf, mcWidthInf, mcBestagonInf,
                    mcTrackless, mcOverlap, mcLoop)
                    VALUES (?,CURRENT_TIMESTAMP,?,?,?,?,
                    ?,?,?,?,
                    ?,?,?,?,
                    ?,?,?,?,
                    ?,?,?,?,?,
                    ?,?,?
                    )"""
            data += [
                metrics['mpCost'], metrics['mpCycles'], metrics['mpArea'], metrics['mpInstructions'],
                metrics['mcCost'], metrics['mcCycles'], metrics['mcArea'], metrics['mcInstructions'],
                metrics['mcHeight'], metrics['mcWidth'], metrics['mcBestagon'], metrics['mcRate'],
                metrics['mcAreaInfLevel'], metrics['mcAreaInfValue'], metrics['mcHeightInf'], metrics['mcWidthInf'], metrics['mcBestagonInf'],
                metrics['mcTrackless'], metrics['mcOverlap'], metrics['mcLoop'],
            ]
            # print(metrics)
        else:
            sql = """INSERT OR REPLACE INTO local
            (solution_file, last_check, solution_name, puzzle_name, valid, omsimtime)
            VALUES (?,CURRENT_TIMESTAMP,?,?,?,?)"""
        db.con.execute(sql, data)
        db.con.commit()


def mismatch(verbose=False):
    # find any puzzles where panic disagrees with OM
    # this should just mean they need to rerun in OM
    sql = """SELECT solution_file, solution_name,
    mpCost, mpCycles, mpArea, mpInstructions,
    mcCost, mcCycles, mcArea, mcInstructions
    FROM local
    WHERE mpCost         != mcCost
    OR    mpCycles       != mcCycles
    OR    mpArea         != mcArea
    OR    mpInstructions != mcInstructions """
    bads = db.con.execute(sql).fetchall()

    if verbose and bads:
        print('Score mismatch found. Please rerun the following in Opus Magnum:')
        for row in bads:
            file = os.path.basename(row[0])
            print('{} "{}" parse({}g/{}c/{}a/{}i) != computed(c{}g/{}c/{}a/{}i)'.format(file, *row[1:]))

    if len(bads) == 0:
        return 'None'
    return len(bads)


def duplicate_scores(verbose=False):
    sql = """SELECT puzzle_name, count(*) as c, group_concat(solution_file,","), group_concat(solution_name,","), * FROM local
WHERE valid
GROUP BY puzzle_name, mcCost, mcCycles, mcArea, mcInstructions, mcHeight, mcWidth, mcRate, mcAreaInfLevel, mcAreaInfValue, mcHeightInf, mcWidthInf, mcTrackless, mcOverlap
HAVING c>1
ORDER BY puzzle_name, solution_name"""
    bads = db.con.execute(sql).fetchall()

    if verbose and bads:
        print("Multiple solutions have the same score.  Maybe this doesn't bother you, but it bugs me.  If they're pareto, it'll be confusing because both will be reported.")
        for bad in bads:
            print(f'The following {bad[1]} files in {bad[0]} have the same scores')
            files = bad[2].split(',')
            names = bad[3].split(',')
            for file, name in zip(files, names):
                file = os.path.basename(file)
                print(f'    {file}, "{name}"')

    return len(bads)


def duplicate_names(verbose=False):
    sql = """SELECT solution_file, puzzle_name, solution_name, count(*) as c FROM local
GROUP BY puzzle_name, solution_name
HAVING c>1
ORDER BY puzzle_name, solution_name"""
    bads = db.con.execute(sql).fetchall()

    if verbose and bads:
        print('Some solutions contain duplicate names which makes it harder to find the one you want.  Consider renaming them?')
        for bad in bads:
            print(f'{os.path.basename(bad[0])}, "{bad[2]}", {bad[3]} copies')

    if len(bads) == 0:
        return 'None'
    return len(bads)


def short_filename(record):
    puzzle_name = zlbb.get_puzzle_name(record[0])
    puzzle_name = puzzle_name.replace('/', '_')

    flags = ''
    if record[16]:
        flags += 'T'
    if record[17]:
        flags += 'O'
    if record[18]:
        flags += 'L'

    #                         End Game-w2946682691-80g-1803c-27a-1814i-6h-5.0w  302.34r-27A0-6H-5.0W-L
    # https://zlbb.faendir.com/l/om/b2671f5a/P036/145g-7i-4049c-33a-7h-6.5w-672r-33a0-7h-6.5w
    r = record
    fstr = f'{puzzle_name}-{r[0]}-{r[3]}g-{r[4]}c-{r[5]}a-{r[6]}i-{r[7]}h-{r[8]}w-{r[9]}b-{r[10]}R-{r[12]}A{r[11]}-{r[13]}H-{r[14]}W-{r[15]}B-{flags}.solution'
    return fstr


def record_string(record):
    puzzle_name = zlbb.get_puzzle_name(record[0])
    file = os.path.basename(record[1])

    flags = ''
    if record[16]:
        flags += 'T'
    if record[17]:
        flags += 'O'
    if record[18]:
        flags += 'L'

    if record[11] == 0:
        aLev = '∞'
    elif record[11] == 1:
        aLev = "'"
    elif record[11] == 2:
        aLev = "''"
    else:
        aLev = 'error'

    r = record  # lazy copy/paste
    fstr = f'{puzzle_name:25} {file:20} "{r[2]}"  {r[3]}g/{r[4]}c/{r[5]}a/{r[6]}i/{r[7]}h/{r[8]}w/{r[9]}b  {r[10]}r/{r[11]}A{aLev}/{r[13]}H∞/{r[14]}W∞/{r[15]}B∞  {flags}'
    # fstr2 = f'{puzzle_name:25}|{file:20}|{r[2]}|{r[3]}|{r[4]}|{r[5]}|{r[6]}|{r[7]}|{r[8]}|{r[9]}|{r[10]}|{r[12]}|{r[13]}|{flags}'
    # print(r)
    return fstr


def get_paretos(verbose=False):
    # todo, I should figure out how to use named factories but they look like a lot of work
    # so, i'm going to be "lazy" and just count out the index of what I need in the touple.
    # sue me

    sql = zlbb.manifold_sql
    paretos = db.con.execute(sql).fetchall()

    if verbose and paretos:
        print('The following are pareto.  Be sure to make sure the leaderboard cache is up to date first.')
        last = paretos[0][0]  # puzzle
        for i, p in enumerate(paretos):

            if last != p[0]:
                # line break between different puzzles
                print()
                last = p[0]
                if i >= 1000:
                    print(f'.. too many to show, {len(paretos)-i} more')
                    break

            if os.path.isdir('pareto'):
                fname = os.path.join('pareto', short_filename(p))
                if not os.path.isfile(fname):
                    shutil.copy(p[1], fname)

            fstr = f'{i+1} {record_string(p)}'
            print(fstr)

        if not os.path.isdir('pareto'):
            print('If you want to get a copy of these, just make a folder named "pareto" and they\'ll be dumped there')
        #  0                    1                      2                   3       4      5     6       7     8     9      10      11    12    13      14     15    16       17        18        19     20       21    22
        # file,                 last_check,           solution,            puzzle, valid, cost, cycles, area, inst, cost,  cycles, area, inst, height, width, rate, areainf, heighinf, widthinf, track, overlap, loop, uptodate
        # ('filename.solution', '2023-05-03 05:50:14', 'NEW SOLUTION F1', 'P090',  1,     245,  461,    83,   140,  '245', '461',  '83', 140,  7,      12.0,  77.0, 83,      7,        12.0,     0,     0,       1,    1)
        # print(paretos[9])
        # todo: find out why some metrics are strings, not numbers
    return paretos


def check_cache(paretos, force=False):
    bads = set(p[0] for p in paretos if p[-1] == 0 or force)
    return bads


def score_part(solution, m_part: str, finite: bool):
    m_part = m_part.lower()
    # todo, stop doing this stupid indexing
    #        0             1          2               3      4        5      6              7        8                  9      10                            11          12                       13          14        15
    # SELECT puzzle_name, 'db/file', 'solution_name', mCost, mCycles, mArea, mInstructions, mHeight, mWidth,            mRate, mAreaInf,                     mHeightInf, mWidthInf,               mTrackless, mOverlap, mLoop from community
    #        0             1          2               3      4        5      6              7        8                  9      10             11             12          13                       14          15        16
    # SELECT puzzle_name, 'db/file', 'solution_name', mCost, mCycles, mArea, mInstructions, mHeight, mWidth,            mRate, mAreaInfLevel, mAreaInfValue  mHeightInf, mWidthInf,               mTrackless, mOverlap, mLoop from community
    #        0             1          2               3      4        5      6              7        8       9          10     11             12             13          14         15            16          17        18
    # SELECT puzzle_name, 'db',      'solution_name', mCost, mCycles, mArea, mInstructions, mHeight, mWidth, mBestagon, mRate, mAreaInfLevel, mAreaInfValue, mHeightInf, mWidthInf, mBestagonInf, mTrackless, mOverlap, mLoop from community
    if m_part == 'g':
        return solution[3]
    if m_part == 'c':
        return solution[4]
    if m_part == 'a':
        if finite:
            return solution[5]
        else:
            return (solution[11], solution[12])
    if m_part == 'i':
        return solution[6]
    if m_part == 'h':
        if finite:
            return solution[7]
        else:
            if solution[13] == 'Inf':
                return math.inf
            return solution[13]
    if m_part == 'w':
        if finite:
            return solution[8]
        else:
            if solution[14] == 'Inf' or solution[14] is None:
                return math.inf
            return solution[14]
    if m_part == 'b':
        if finite:
            return solution[9]
        else:
            if solution[15] == 'Inf' or solution[15] is None:
                return math.inf
            return solution[15]
    if m_part == 'r':
        if solution[10] == 'Inf':
            return math.inf
        return solution[10]
    if m_part == 't':
        return -solution[16]
    if m_part == 'ti':
        return (score_part(solution, 't', finite), score_part(solution, 'i', finite))
    if m_part == 'o':
        return 0
    if m_part in ('!o', ''):
        return solution[17]
    if m_part == 'l':
        return -solution[18]

    assert False, f'Unrecognized metric: "{m_part}"'
    return 0


def score_whole(solution, metric):
    finite = "VICTORY" in metric["manifold"]["id"]
    ss = []

    for m in metric['fullmetrics']:
        if '+' in m:
            s = 0
            for mm in m.split('+'):
                s += score_part(solution, mm, finite)
        elif '·' in m:
            s = 1
            for mm in m.split('·'):
                s *= score_part(solution, mm, finite)
        else:
            s = score_part(solution, m, finite)
        ss.append(s)

    # I don't know what the default tie breaker metrics so just existing solutions
    # todo: add a parameter to allow reporting those that tie existing records
    ss.append(solution[1] != 'db')  # db wins ties
    # ss.append(solution[1] == 'db') # local wins ties

    return tuple(ss)


def get_records(verbose=False):
    puzzles = set([p[0] for p in paretos])
    recs = dict()
    for puzzle in puzzles:
        sql = """SELECT puzzle_name, 'db', 'solution_name', mCost, mCycles, mArea, mInstructions, mHeight, mWidth, mBestagon, mRate, mAreaInfLevel, mAreaInfValue, mHeightInf, mWidthInf, mBestagonInf, mTrackless, mOverlap, mLoop from community
        WHERE puzzle_name = ?"""
        existing = db.con.execute(sql, [puzzle]).fetchall()
        potential = [tuple(p) for p in paretos if p[0] == puzzle]
        both = existing + potential

        for metric in zlbb.categories:
            if zlbb.puzzles[puzzle]['type'] not in metric['puzzleTypes']:
                continue
            best = sorted((score_whole(sol, metric), sol, sol[1]) for sol in both)[0]

            if best[-1] != 'db':
                recs.setdefault(best[1], []).append(metric['displayName'])

    srecs = sorted([(rec, sorted(cats)) for rec, cats in recs.items()], key=lambda x: (x[0][0], x[1], x))

    if verbose:
        for s in srecs:
            cats = ', '.join(s[1])
            fstr = record_string(s[0])
            print(f'{cats:20} : {fstr}')

            p = s[0]
            if os.path.isdir('record'):
                fname = os.path.join('record', short_filename(p))
                if not os.path.isfile(fname):
                    shutil.copy(p[1], fname)

    return srecs


if __name__ == '__main__':
    tracked_puzzles = [f for f in os.listdir('puzzle') if f.endswith('.puzzle') and f[:-7] in zlbb.puzzles]
    print(len(tracked_puzzles), 'tracked puzzles')
    # todo: compare against website list and report any diff

    local_stats = scan_local()
    while True:
        paretos = get_paretos()
        bad_cache = check_cache(paretos)
        bad_cache2 = check_cache(paretos, True)
        records = get_records()

        if len(sys.argv) > 1:
            m = sys.argv[1]
        else:
            print('---------- Menu ----------')
            print(f'1. Rescan solutions ({len(local_stats["local"])} files found, {len(local_stats["db"])} tracked)')  # TODO: remove this

            proc = ', '.join([f'{len(local_stats[cat])} {cat}' for cat in ['new', 'update', 'delete', 'oversized'] if len(local_stats[cat]) > 0])
            if proc == '':
                proc = 'None'
            print(f'2. Process ({proc})')

            # reports
            print(f'3. Mismatched scores ({mismatch()})')
            print(f'4. Duplicate scores ({duplicate_scores()})')
            print(f'5. Duplicate names ({duplicate_names()})')

            # paretos:
            print(f'6. Refresh leaderboard cache ({len(bad_cache)})')
            print(f'7. Force leaderboard cache ({len(bad_cache2)})')
            print(f'8. Pareto ({len(paretos)})')
            # todo, make a mode that prioritizes solutions that kill existing paretos.  kind of hard with multiple manifolds though

            print(f'9. Just records ({len(records)})')

            # print('0. Process -> update cache -> report paretos')

            print('x. Exit')
            # todo, add overlap hermit mode warning

            m = input()
        if m == '1':
            local_stats = scan_local()
        elif m == '2':
            process_solutions()
            local_stats = scan_local()
        elif m == '3':
            mismatch(True)
        elif m == '4':
            duplicate_scores(True)
        elif m == '5':
            duplicate_names(True)

        elif m == '6':
            # todo: add some kind of timeout to check leaderboard if it's been a while
            # even if the current cache is newer than the last file changes
            for bc in check_cache(get_paretos()):
                zlbb.update_community(bc)
        elif m == '7':
            for bc in check_cache(get_paretos(), True):
                zlbb.update_community(bc)
        elif m == '7.':
            for bc in sorted(set(r[0][0] for r in get_records())):
                zlbb.update_community(bc)
        elif m == '8':
            get_paretos(True)
        elif m == '9':
            get_records(True)

        elif m == '0':  # rerun all new, check the leaderboard, report
            process_solutions()
            for bc in check_cache(get_paretos()):
                zlbb.update_community(bc)
            get_paretos(True)
            local_stats = scan_local()

        elif m.lower() == 'x' or m == '':
            break

        elif m == 'updateall':
            # purge and get all the leaderboard
            zlbb.update_all()
        else:
            print('Unrecognized selection')

        if len(sys.argv) > 1:
            break
