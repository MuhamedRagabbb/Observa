"""Offline audit helper: reconstruct a v1 snapshot losslessly. Not an agent input tool."""
from .evidence import read_evidence


def restore_snapshot(reports, incident_id, snapshot_id):
    def rows(ref):
        items, cursor = [], 0
        while True:
            page = read_evidence(reports, incident_id, snapshot_id, ref,
                                 cursor=cursor, limit=100, max_bytes=65536)
            if page['error']:
                raise ValueError('Audit cannot reconstruct oversized evidence record')
            items.extend(page['items'])
            if page['next_cursor'] is None:
                return items
            cursor = page['next_cursor']
    details = rows('details')
    result = details[0]['value']
    result['alerts'] = {row['alert_id']: row['value'] for row in details[1:]}
    result['relationships'] = rows('relationships')
    result['timeline'] = rows('timeline')
    traces = []
    for entry in rows('traces'):
        page = read_evidence(reports, incident_id, snapshot_id, entry['ref_id'])
        traces.append({**page['metadata'], 'spans': rows(entry['ref_id'])})
    result['evidence'] = {'logs': rows('logs'), 'traces': traces}
    return result
