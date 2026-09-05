import time
import random
import platform
import networkx as nx
import timeout_decorator
from simpleai.search import CspProblem, backtrack

from agent_baselines import Agent

TIME_BUDGET = 0.80  # hard ceiling is 1s; leave real headroom for engine overhead

'''
WINDOWS COMPATIBILITY NOTE:
    The timeout_decorator package may not work correctly on Windows. For local
    development on Windows, you may comment out the import and all four
    @timeout_decorator.timeout(1) lines in this file. If you do so, measure the
    running time of __init__, new_game, update_game, and get_actions yourself
    (for example, with time.perf_counter). This local workaround does not relax
    the one-second limit: it is a hard constraint and will be enforced
    independently during marking.

    THIS FILE ALREADY IMPLEMENTS THE ABOVE AUTOMATICALLY, so there is nothing
    to manually comment/uncomment (and nothing to remember to restore before
    submission). Below, `_safe_timeout` checks platform.system(): on
    Linux/Mac (i.e. how it will actually run on the marking machine) it is
    exactly timeout_decorator.timeout(1), unchanged from what's required; on
    Windows it silently falls back to a no-op so local development doesn't
    crash on the missing signal.SIGALRM. This does NOT relax the 1s limit
    for marking -- it only avoids the Windows-specific crash locally.
'''
from agent_baselines import Agent

if platform.system() == 'Windows':
    def _safe_timeout(seconds):
        def decorator(func):
            return func
        return decorator
else:
    def _safe_timeout(seconds):
        return timeout_decorator.timeout(seconds)


class StudentAgent(Agent):
    '''
    Implement your agent here.

    Please read the abstract Agent class from agent_baselines.py first.

    You can add/override attributes and methods as needed.

    ------------------------------------------------------------------
    IMPLEMENTATION OVERVIEW (see individual method docstrings for detail)
    ------------------------------------------------------------------
      BASIC TECHNIQUE
        Greedy, strategic-value-weighted order assignment (_greedy_initial_assignment).
        For each unit, move toward the reachable target that maximises
        (strategic value / distance), where strategic value comes from a
        graph-based heuristic (see _compute_strategic_values). This directly
        extends the taught idea of greedy best-first search to this domain.

      NEW TECHNIQUE 1 -- Iterative local search (hill-climbing)
        (_local_search) A variant of the search process applied on top of
        the basic technique: after the greedy pass, randomly perturb
        individual unit orders and keep changes that improve a heuristic
        evaluation of the whole joint assignment (_evaluate_assignment).

      NEW TECHNIQUE 2 -- CSP-based support coordination
        (_csp_assign_supports) A genuine constraint satisfaction formulation
        (using simpleai's CspProblem/backtrack) for deciding which otherwise-
        idle units should support attacks vs. defend threatened home centres,
        instead of the ad-hoc pairing baseline GreedyAgent uses.

      NEW TECHNIQUE 3 -- Opponent-adaptive targeting & defence
        (_hostility, _estimate_threat_map, _compute_strategic_values)
        Tracks a rolling behavioural profile per opponent power (attacks /
        supports directed at us) and uses it to bias targeting (avoid
        provoking friendly powers, prioritise hostile ones) and to raise
        defensive weighting around genuinely threatened home centres.
    '''

    @_safe_timeout(1)
    def __init__(self, agent_name='GroupX Agent'):
        super().__init__(agent_name)
        self.map_graph_army = None
        self.map_graph_navy = None
        self.strategic_value = {}
        self.opponent_stats = {}

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #

    @_safe_timeout(1)
    def new_game(self, game, power_name):
        self.game = game
        self.power_name = power_name
        self._build_map_graphs()
        self._compute_strategic_values()
        self.opponent_stats = {
            p: {'attacks_on_us': 0, 'supports_of_us': 0, 'moves': 0}
            for p in self.game.powers.keys() if p != self.power_name
        }

    def _build_map_graphs(self):
        self.map_graph_army = nx.Graph()
        self.map_graph_navy = nx.Graph()
        locations = list(self.game.map.loc_type.keys())

        for i in locations:
            if self.game.map.loc_type[i] in ['LAND', 'COAST']:
                self.map_graph_army.add_node(i.upper())
            if self.game.map.loc_type[i] in ['WATER', 'COAST']:
                self.map_graph_navy.add_node(i.upper())

        locations = [i.upper() for i in locations]
        for i in locations:
            for j in locations:
                if self.game.map.abuts('A', i, '-', j):
                    self.map_graph_army.add_edge(i, j)
                if self.game.map.abuts('F', i, '-', j):
                    self.map_graph_navy.add_edge(i, j)

    def _compute_strategic_values(self):
        '''
        Graph-based strategic value: hub centres (bordering many other
        centres) and well-connected provinces are worth more than isolated
        ones. Feeds both the basic technique and the evaluation function.
        '''
        scs = set(self.game.map.scs)
        self.strategic_value = {}
        for sc in scs:
            sc_u = sc.upper()
            neighbours = list(self.map_graph_army.neighbors(sc_u)) if sc_u in self.map_graph_army else []
            adj_scs = sum(1 for n in neighbours if n in scs)
            self.strategic_value[sc_u] = 1.0 + 0.5 * adj_scs + 0.3 * len(neighbours)

    def _value_of(self, loc):
        return self.strategic_value.get(loc.upper(), 1.0)

    # ------------------------------------------------------------------ #
    # Robust order parsing (fixes fragile string-splitting bugs where
    # convoy orders were mis-read as attack moves, and support/convoy
    # orders referencing FOREIGN units were treated as our own actions)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_order(order):
        words = order.split(' ')
        unit_loc = words[1]
        if len(words) == 3 and words[2] == 'H':
            return {'type': 'hold', 'unit_loc': unit_loc}
        if words[2] == '-':
            return {'type': 'move', 'unit_loc': unit_loc, 'dest': words[3]}
        if words[2] == 'S':
            supported_loc = words[4]
            if len(words) >= 7 and words[5] == '-':
                return {'type': 'support_move', 'unit_loc': unit_loc,
                        'supported_loc': supported_loc, 'dest': words[6]}
            return {'type': 'support_hold', 'unit_loc': unit_loc, 'supported_loc': supported_loc}
        if words[2] == 'C':
            return {'type': 'convoy', 'unit_loc': unit_loc,
                    'convoyed_loc': words[4], 'dest': words[6]}
        if words[2] == 'R':
            return {'type': 'retreat', 'unit_loc': unit_loc, 'dest': words[3]}
        if words[2] == 'B':
            return {'type': 'build', 'unit_loc': unit_loc}
        if words[2] == 'D':
            return {'type': 'disband', 'unit_loc': unit_loc}
        return {'type': 'unknown', 'unit_loc': unit_loc}

    def _filter_own_relevant_orders(self, possible_orders):
        '''
        The engine returns every legal order for each of our units, including
        support/convoy orders that reference units belonging to OTHER powers
        (legal under the rules -- real Diplomacy allows supporting an ally --
        but useless and misleading for a No-Press bot with no coordination:
        such orders are void unless that foreign power happens to issue the
        exact matching move, which we cannot arrange). We drop these options
        entirely so every downstream stage (greedy, CSP, local search) only
        ever considers actions that actually make sense for us to take.
        '''
        my_unit_locs = {u.split(' ')[1] for u in self.game.get_units(self.power_name)}
        filtered = {}
        for loc, options in possible_orders.items():
            kept = []
            for o in options:
                parsed = self._parse_order(o)
                if parsed['type'] == 'support_move' or parsed['type'] == 'support_hold':
                    if parsed['supported_loc'] not in my_unit_locs and parsed['supported_loc'] != loc:
                        continue
                elif parsed['type'] == 'convoy':
                    if parsed['convoyed_loc'] not in my_unit_locs:
                        continue
                kept.append(o)
            filtered[loc] = kept
        return filtered

    # ------------------------------------------------------------------ #
    # Opponent modelling (New Technique 3, part A)
    # ------------------------------------------------------------------ #

    @_safe_timeout(1)  # This is only for updating the game engine and other states if any. Do not implement heavy stratergy here.
    def update_game(self, all_power_orders):
        # Lightweight bookkeeping only (see docstring: New Technique 3) --
        # this just counts attacks/supports directed at us, it does not run
        # any search or heavy computation.
        if self.game.phase_type == 'M':
            self_locs = set(self.game.get_centers(self.power_name) +
                             self.game.get_orderable_locations(self.power_name))
            self._update_opponent_stats(all_power_orders, self_locs)

        # do not make changes to the following codes
        for power_name in all_power_orders.keys():
            self.game.set_orders(power_name, all_power_orders[power_name])
        self.game.process()

    def _update_opponent_stats(self, all_power_orders, self_locs):
        for p, orders in all_power_orders.items():
            if p == self.power_name:
                continue
            stats = self.opponent_stats.setdefault(
                p, {'attacks_on_us': 0, 'supports_of_us': 0, 'moves': 0})
            for order in orders:
                parsed = self._parse_order(order)
                if parsed['type'] == 'move':
                    stats['moves'] += 1
                    if parsed['dest'] in self_locs:
                        stats['attacks_on_us'] += 1
                elif parsed['type'] == 'support_move':
                    if parsed['dest'] in self_locs:
                        stats['supports_of_us'] += 1

    def _hostility(self, power):
        '''[-1, 1]: positive = hostile (attacks us a lot), negative = friendly.'''
        s = self.opponent_stats.get(power)
        if not s or s['moves'] == 0:
            return 0.0
        score = (s['attacks_on_us'] - s['supports_of_us']) / max(1, s['moves'])
        return max(-1.0, min(1.0, score))

    # ------------------------------------------------------------------ #
    # Threat estimation (New Technique 3, part B)
    # ------------------------------------------------------------------ #

    def _estimate_threat_map(self):
        '''
        For every one of our units/centres, estimate how many enemy units
        could move onto it next turn (a proxy for "under attack"). Used by
        both the basic technique (avoid over-extending) and the CSP support
        technique (prioritise defence).
        '''
        my_locs = set(self.game.get_centers(self.power_name) +
                      self.game.get_orderable_locations(self.power_name))
        threat = {loc: 0 for loc in my_locs}

        for p in self.game.powers.keys():
            if p == self.power_name:
                continue
            hostility = self._hostility(p)
            # Only count a neighbouring unit as a real threat once that power
            # has shown actual aggression (hostility > 0). Purely passive
            # neighbours (e.g. StaticAgent, or anyone who hasn't attacked us
            # yet) contribute ~0, so we don't over-defensively hole up against
            # opponents who were never going to attack.
            weight = max(0.0, hostility)
            if weight == 0.0:
                continue
            for unit in self.game.get_units(p):
                u_loc = unit.split(' ')[1]
                graph = self.map_graph_army if unit[0] == 'A' else self.map_graph_navy
                if u_loc not in graph:
                    continue
                for n in graph.neighbors(u_loc):
                    if n in threat:
                        threat[n] += weight
        return threat

    # ------------------------------------------------------------------ #
    # Action dispatch
    # ------------------------------------------------------------------ #

    @_safe_timeout(1)
    def get_actions(self):
        '''
        Return a list of orders. Each order is a string, with specific format. For the format, read the game rule and game engine documentation.
        
        Expected format:
        A LON H                  # Army at LON holds
        F IRI - MAO              # Fleet at IRI moves to MAO (and attack)
        A WAL S F LON            # Army at WAL supports Fleet at LON (and hold)
        F NTH S A EDI - YOR      # Fleet at NTH supports Army at EDI to move to YOR
        F NWG C A NWY - EDI      # Fleet at NWG convoys Army at NWY to EDI
        A NWY - EDI VIA          # Army at NWY moves to EDI via convoy
        A WAL R LON              # Army at WAL retreats to LON
        A LON D                  # Disband Army at LON
        A LON B                  # Build Army at LON
        F EDI B                  # Build Fleet at EDI

        Note: If an invalid order is sent to the engine, it will be accepted but with a result of 'void' (no effect).
        Note: For a 'support' action, two orders are needed, one for the supporter and one for the supportee. (Same for 'convoy')
        Note: For each unit, if no order is given, it will 'hold' by default.

        Useful Functions:
        
        # This is a dict of all the possible orders for each unit at each location (for all powers).
        possible_orders = self.game.get_all_possible_orders()

        # This is a list of all orderable locations for the power you control.
        orderable_locations = self.game.get_orderable_locations(self.power_name)
    
        # Combining these two, you can have the full action space for the power you control.

        # You can re-use the build_map_graphs function in the GreedyAgent to build the connection graph of the map if needed.
        
        '''
                
        start = time.perf_counter()
        phase_type = self.game.phase_type

        if phase_type == 'M':
            return self._get_movement_orders(start)
        elif phase_type == 'R':
            return self._get_retreat_orders()
        elif phase_type == 'A':
            return self._get_adjustment_orders()
        return []

    # ---------------------- Movement phase --------------------------- #

    def _get_movement_orders(self, start_time):
        possible_orders = self.game.get_all_possible_orders()
        orderable_locations = self.game.get_orderable_locations(self.power_name)
        if not orderable_locations:
            return []

        # Restrict to orders that reference only our own units (see
        # _filter_own_relevant_orders docstring for why this matters).
        possible_orders = self._filter_own_relevant_orders(possible_orders)

        my_centers = set(self.game.get_centers(self.power_name))
        enemy_or_neutral_centers = [c for c in self.game.map.scs if c not in my_centers]

        loc_owner = {}
        for p in self.game.powers.keys():
            for loc in set(self.game.get_centers(p) + self.game.get_orderable_locations(p)):
                loc_owner[loc] = p

        threat_map = self._estimate_threat_map()

        # ---- BASIC TECHNIQUE: greedy weighted assignment ----
        assignment, committed = self._greedy_initial_assignment(
            orderable_locations, possible_orders, enemy_or_neutral_centers,
            loc_owner, threat_map)

        # ---- De-confliction: if two of our own units would move to the same
        # destination, that's a guaranteed self-bounce (wasted moves). Convert
        # all but one into a support for that one, when legal.
        assignment, committed, committed_destinations = self._resolve_self_conflicts(
            assignment, committed, possible_orders)

        # ---- NEW TECHNIQUE 2: CSP-based support coordination ----
        # committed_destinations = provinces our attacking units are moving
        # into; everything else is free to support one of those attacks or
        # defend a threatened home centre.
        assignment = self._csp_assign_supports(
            assignment, committed, committed_destinations, possible_orders,
            orderable_locations, threat_map)

        # ---- NEW TECHNIQUE 1: bounded local search on top of both ----
        assignment = self._local_search(assignment, possible_orders, orderable_locations,
                                         start_time, threat_map)

        return list(assignment.values())

    def _greedy_initial_assignment(self, orderable_locations, possible_orders,
                                    enemy_or_neutral_centers, loc_owner, threat_map):
        assignment = {}
        committed_movers = set()  # locations we've assigned an attack move to

        for loc in orderable_locations:
            options = possible_orders.get(loc, [])
            if not options:
                continue

            army_opts = [o for o in options if o[0] == 'A']
            navy_opts = [o for o in options if o[0] == 'F']
            unit_opts = army_opts if army_opts else navy_opts
            graph = self.map_graph_army if army_opts else self.map_graph_navy
            unit_type = 'A' if army_opts else 'F'

            if not unit_opts:
                assignment[loc] = random.choice(options)
                continue

            # Defensive override: if this location is one of our centres and
            # under heavy threat, prefer holding (so it can later receive
            # support from the CSP stage) rather than wandering off.
            if loc in my_centers_threatened_hold_candidate(self, loc, threat_map):
                hold = f'{unit_type} {loc} H'
                if hold in options:
                    assignment[loc] = hold
                    continue

            if loc in enemy_or_neutral_centers:
                hold = f'{unit_type} {loc} H'
                assignment[loc] = hold if hold in options else unit_opts[0]
                continue

            best_target, best_score = None, float('-inf')
            if loc in graph:
                paths = nx.shortest_path(graph, source=loc)
                for center in enemy_or_neutral_centers:
                    if center not in paths:
                        continue
                    dist = len(paths[center]) - 1
                    if dist == 0:
                        continue
                    owner = loc_owner.get(center)
                    hostility = self._hostility(owner) if owner else 0.0
                    score = self._value_of(center) / (dist ** 1.2) + 0.3 * hostility
                    if score > best_score:
                        best_score, best_target = score, center

            if best_target is not None:
                next_hop = nx.shortest_path(graph, source=loc, target=best_target)[1]
                order = f'{unit_type} {loc} - {next_hop}'
                if order in options:
                    assignment[loc] = order
                    committed_movers.add(loc)
                else:
                    assignment[loc] = random.choice(unit_opts)
            else:
                hold = f'{unit_type} {loc} H'
                assignment[loc] = hold if hold in options else random.choice(unit_opts)

        return assignment, committed_movers

    def _resolve_self_conflicts(self, assignment, committed_movers, possible_orders):
        '''
        If two or more of our own committed attacking units share the same
        destination, they will bounce each other for free (a pure self-
        inflicted waste, since it's an unsupported clash with strength 1v1).
        Convert all but one mover into a support order for the remaining
        mover when a legal support order exists; otherwise fall back to Hold
        so the unit isn't wasted on a guaranteed-failed move.
        '''
        dest_to_movers = {}
        for loc in committed_movers:
            words = assignment[loc].split(' ')
            dest = words[words.index('-') + 1]
            dest_to_movers.setdefault(dest, []).append(loc)

        still_committed = set(committed_movers)
        for dest, movers in dest_to_movers.items():
            if len(movers) < 2:
                continue
            keeper = movers[0]
            keeper_words = assignment[keeper].split(' ')
            for other in movers[1:]:
                unit_type = assignment[other].split(' ')[0]
                support_order = f'{unit_type} {other} S {keeper_words[0]} {keeper_words[1]} - {dest}'
                options = possible_orders.get(other, [])
                if support_order in options:
                    assignment[other] = support_order
                else:
                    hold = f'{unit_type} {other} H'
                    assignment[other] = hold if hold in options else assignment[other]
                still_committed.discard(other)

        committed_destinations = set()
        for loc in still_committed:
            words = assignment[loc].split(' ')
            committed_destinations.add(words[words.index('-') + 1])

        return assignment, still_committed, committed_destinations

    # ---------------- NEW TECHNIQUE 2: CSP support coordination -------- #

    def _csp_assign_supports(self, assignment, committed_movers, committed_destinations,
                              possible_orders, orderable_locations, threat_map):
        '''
        Formulates support/hold/defence assignment for units NOT already
        given an attacking move as a constraint satisfaction problem:

          Variables: units without a committed attack move.
          Domains:   {legal support-move orders for committed attacks} +
                     {legal support-hold orders for threatened own centres} +
                     {Hold}.
          Constraint: no more than `max_support_per_target` units may support
                      the same target (prevents wasteful over-stacking of
                      support onto an already-safe attack when other units
                      could instead defend elsewhere).

        Solved with simpleai's backtracking search. The variable set is
        small (only uncommitted units), so this stays comfortably inside the
        time budget; if it's ever unexpectedly large we fall back to a fast
        greedy assignment instead of risking the 1s limit.
        '''
        free_units = [loc for loc in orderable_locations
                      if loc not in committed_movers and loc in assignment]

        if not free_units:
            return assignment

        max_support_per_target = 2

        # Build domains: for each free unit, which support/hold orders can it
        # legally take that are relevant (i.e. support one of our committed
        # attacks, or hold/support-hold a threatened centre of ours)?
        my_centers = set(self.game.get_centers(self.power_name))
        threatened_centers = {loc for loc, t in threat_map.items()
                               if loc in my_centers and t > 0}

        # map each committed mover's location -> its actual chosen destination,
        # so we only ever support the SPECIFIC move we decided to make (not
        # just any order that happens to end at the same destination string)
        committed_move_dest = {loc: self._parse_order(assignment[loc])['dest']
                                for loc in committed_movers}

        domains = {}
        for loc in free_units:
            options = possible_orders.get(loc, [])
            relevant = []
            for o in options:
                parsed = self._parse_order(o)
                if parsed['type'] == 'support_move':
                    s_loc = parsed['supported_loc']
                    if (s_loc in committed_movers and
                            committed_move_dest.get(s_loc) == parsed['dest']):
                        relevant.append(o)
                elif parsed['type'] == 'support_hold':
                    s_loc = parsed['supported_loc']
                    if s_loc in threatened_centers or s_loc in my_centers:
                        relevant.append(o)
                elif parsed['type'] == 'hold':
                    relevant.append(o)
            if not relevant:
                relevant = [assignment[loc]]  # keep whatever greedy chose
            domains[loc] = list(dict.fromkeys(relevant))  # de-dup, preserve order

        # Safety valve: if the CSP would be too large to search comfortably,
        # skip straight to the (already-valid) greedy assignment for these
        # units rather than risking the time budget.
        problem_size = sum(len(d) for d in domains.values())
        if len(free_units) > 25 or problem_size > 300:
            return assignment

        def target_of(order):
            words = order.split(' ')
            if ' S ' in order and '-' in words:
                return words[-1]
            if ' S ' in order:
                return words[words.index('S') + 2]
            return None

        def support_cap_constraint(variables, values):
            target_counts = {}
            for v in values:
                t = target_of(v)
                if t is None:
                    continue
                target_counts[t] = target_counts.get(t, 0) + 1
                if target_counts[t] > max_support_per_target:
                    return False
            return True

        constraints = [(tuple(free_units), support_cap_constraint)]

        try:
            problem = CspProblem(free_units, domains, constraints)
            result = backtrack(problem, inference=True)
        except Exception:
            result = None

        if result:
            for loc, order in result.items():
                assignment[loc] = order

        return assignment

    # ---------------- NEW TECHNIQUE 1: local search -------------------- #

    def _local_search(self, assignment, possible_orders, orderable_locations, start_time, threat_map):
        best_score = self._evaluate_assignment(assignment, threat_map)
        # scale iterations with unit count so larger empires still get
        # reasonable per-unit search coverage, capped for safety
        max_iters = min(200, max(60, 8 * len(orderable_locations)))

        for _ in range(max_iters):
            if time.perf_counter() - start_time > TIME_BUDGET:
                break

            loc = random.choice(orderable_locations)
            options = possible_orders.get(loc, [])
            if len(options) < 2:
                continue

            current = assignment.get(loc)
            candidate = random.choice(options)
            if candidate == current:
                continue

            assignment[loc] = candidate
            score = self._evaluate_assignment(assignment, threat_map)
            # strict improvement only -- accepting ties just adds noise /
            # turn-to-turn oscillation with no actual benefit
            if score > best_score:
                best_score = score
            else:
                assignment[loc] = current

        return assignment

    def _evaluate_assignment(self, assignment, threat_map=None):
        threat_map = threat_map or {}
        score = 0.0
        support_count = {}
        move_targets = {}  # destination -> list of our own units moving there

        for loc, order in assignment.items():
            parsed = self._parse_order(order)
            if parsed['type'] == 'support_move':
                support_count[parsed['dest']] = support_count.get(parsed['dest'], 0) + 1
            elif parsed['type'] == 'support_hold':
                support_count[parsed['supported_loc']] = support_count.get(parsed['supported_loc'], 0) + 1
            elif parsed['type'] == 'move':
                move_targets.setdefault(parsed['dest'], []).append(loc)
            # 'convoy', 'hold', and anything else contribute no direct score
            # here (a convoy's value shows up via the move it enables, which
            # is scored separately as that unit's own 'move' order)

        occupied = set()
        for p in self.game.powers.keys():
            for u in self.game.get_units(p):
                occupied.add(u.split(' ')[1])

        for target, movers in move_targets.items():
            support = support_count.get(target, 0)
            value = self._value_of(target)

            if len(movers) > 1:
                # Two or more of our OWN units targeting the same destination
                # is always wasteful: with no third party involved, this is
                # an unsupported 1v1 (or worse) self-bounce -- nobody moves.
                # Only the redundant extra movers are penalised; if these
                # units had instead supported one another this wouldn't
                # trigger (support orders aren't counted as move_targets).
                score -= value * 0.75 * (len(movers) - 1)
                continue

            if target in occupied:
                score += value * (1 + 0.5 * support) if support > 0 else -value * 0.75
            else:
                score += value

        # Defensive bonus for holding/supporting a home centre ONLY applies
        # when that centre is under genuine, observed threat -- otherwise
        # this would create a constant artificial pull toward fortifying
        # already-safe territory instead of continuing to expand.
        my_centers = set(self.game.get_centers(self.power_name))
        for loc, order in assignment.items():
            if loc in my_centers and threat_map.get(loc, 0) > 0 and (' H' in order or ' S ' in order):
                bonus = 0.3 + 0.4 * support_count.get(loc, 0)
                score += bonus * self._value_of(loc)

        return score

    # ---------------------- Retreat phase ----------------------------- #

    def _get_retreat_orders(self):
        possible_orders = self.game.get_all_possible_orders()
        orders = []
        my_centers = set(self.game.get_centers(self.power_name))
        for loc in self.game.get_orderable_locations(self.power_name):
            options = possible_orders.get(loc, [])
            if not options:
                continue
            retreat_opts = [o for o in options if ' R ' in o]
            if not retreat_opts:
                orders.append(options[0])
                continue

            def retreat_score(o):
                dest = o.split(' ')[-1]
                base = self._value_of(dest)
                if dest in my_centers:
                    base += 2.0
                return base

            orders.append(max(retreat_opts, key=retreat_score))
        return orders

    # ---------------------- Adjustment phase --------------------------- #

    def _get_adjustment_orders(self):
        possible_orders = self.game.get_all_possible_orders()
        orderable_locations = self.game.get_orderable_locations(self.power_name)
        centers = self.game.get_centers(self.power_name)
        units = self.game.get_units(self.power_name)
        threat_map = self._estimate_threat_map()

        orders = []
        if len(centers) >= len(units):
            ranked = sorted(orderable_locations, key=lambda l: -threat_map.get(l, 0))
            for loc in ranked:
                options = possible_orders.get(loc, [])
                build_opts = [o for o in options if o.endswith(' B')]
                if build_opts:
                    orders.append(build_opts[0] if len(build_opts) == 1 else
                                  self._choose_build_type(loc, build_opts))
                elif options:
                    orders.append(options[0])
        else:
            ranked = sorted(orderable_locations, key=lambda l: self._value_of(l))
            for loc in ranked:
                options = possible_orders.get(loc, [])
                disband_opts = [o for o in options if o.endswith(' D')]
                if disband_opts:
                    orders.append(disband_opts[0])
                elif options:
                    orders.append(options[0])
        return orders

    def _choose_build_type(self, loc, build_opts):
        navy_opt = next((o for o in build_opts if o.startswith('F')), None)
        army_opt = next((o for o in build_opts if o.startswith('A')), None)
        if navy_opt and loc in self.map_graph_navy:
            water_neighbours = [n for n in self.map_graph_navy.neighbors(loc)
                                 if self.game.map.loc_type.get(n.lower(), '') == 'WATER']
            if water_neighbours:
                return navy_opt
        return army_opt or build_opts[0]


def my_centers_threatened_hold_candidate(agent, loc, threat_map):
    '''Helper: is `loc` one of our centres under significant, credible threat?'''
    my_centers = set(agent.game.get_centers(agent.power_name))
    if loc in my_centers and threat_map.get(loc, 0) >= 1.5:
        return {loc}
    return set()
