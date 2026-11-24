"""HSA-id and legitimation of a professional (#1054, record.pdhc stage 2).

record.pdhc renders "ansvarig med yrkesroll" and decides who may sign from
these, together with the affiliation role per care unit (already in the blob,
`affiliations[].role`). Only an SU sets them; every change is audited.

HSA-id format
-------------
`SE` + a 10-digit organisation number + `-` + 1 to 18 letters or digits, at
most 31 characters in all (Inera's HSA-id format: country code, the issuing
organisation's number, a serial part; 31 characters is the stated maximum, so
the serial part can be at most 31 - 13 = 18). Stored UPPER-CASE, so uniqueness
does not depend on how it was typed. Example: `SE5565594230-SYNT0001`.

Synthetic rule (D8: the pilot uses synthetic HSA-ids, real ones at level B)
-----------------------------------------------------------------------------
While `HSA_ID_SYNTHETIC_ONLY` is true (the default) the serial part must start
with `HSA_ID_SYNTHETIC_PREFIX` (default `SYNT`). Anything else is refused as
"not synthetic" — a real HSA-id cannot be told from a made-up one by its
shape, so the marker is the only safe test, and it is a positive one: the id
says it is synthetic, rather than us guessing that it is not real. The blob
carries `hsa_id_synthetic` so record.pdhc can mark it on a printout.

Legitimation
------------
A code from Socialstyrelsen's list of the 22 legitimationsyrken
(patientsäkerhetslagen 2010:659, 4 kap. 1 §), or none. `legitimation_verified`
is false: the pilot has no lookup against Socialstyrelsen's register, so
nothing sets it true, and changing `legitimation` resets it to false.
"""
import re

from sqlalchemy.exc import IntegrityError

from src.models.professional import Professional
from src.models.professional_qualification import ProfessionalQualification
from src.models.user import User
from src.services.audit_log import audit

#: Socialstyrelsen's legitimationsyrken. Code -> Swedish name. ASCII codes so
#: they survive every transport; the name is what a printout shows.
LEGITIMATIONSYRKEN = {
    'apotekare': 'Apotekare',
    'arbetsterapeut': 'Arbetsterapeut',
    'audionom': 'Audionom',
    'barnmorska': 'Barnmorska',
    'biomedicinsk_analytiker': 'Biomedicinsk analytiker',
    'dietist': 'Dietist',
    'fysioterapeut': 'Fysioterapeut',
    'halso_och_sjukvardskurator': 'Hälso- och sjukvårdskurator',
    'kiropraktor': 'Kiropraktor',
    'logoped': 'Logoped',
    'lakare': 'Läkare',
    'naprapat': 'Naprapat',
    'optiker': 'Optiker',
    'ortopedingenjor': 'Ortopedingenjör',
    'psykolog': 'Psykolog',
    'psykoterapeut': 'Psykoterapeut',
    'receptarie': 'Receptarie',
    'rontgensjukskoterska': 'Röntgensjuksköterska',
    'sjukhusfysiker': 'Sjukhusfysiker',
    'sjukskoterska': 'Sjuksköterska',
    'tandhygienist': 'Tandhygienist',
    'tandlakare': 'Tandläkare',
}

HSA_ID_MAX = 31
_HSA_ID = re.compile(r'^SE(\d{10})-([A-Z0-9]{1,18})\Z')


class QualificationRefused(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _truthy(value):
    return str(value if value is not None else '').strip().lower() in ('1', 'true', 'yes')


def synthetic_only(config):
    return _truthy(config.get('HSA_ID_SYNTHETIC_ONLY', 'true'))


def synthetic_prefix(config):
    return (config.get('HSA_ID_SYNTHETIC_PREFIX') or 'SYNT').strip().upper()


def is_synthetic(hsa_id, config):
    m = _HSA_ID.match(hsa_id or '')
    return bool(m) and m.group(2).startswith(synthetic_prefix(config))


def normalise_hsa_id(value, config):
    """Canonical upper-case HSA-id, None for blank, or QualificationRefused(400)."""
    raw = (value or '').strip() if isinstance(value, str) else ''
    if not raw:
        return None
    hsa_id = raw.upper()
    if len(hsa_id) > HSA_ID_MAX or not _HSA_ID.match(hsa_id):
        raise QualificationRefused(
            400, 'invalid_hsa_id',
            'HSA-id must be SE + 10-digit organisation number + "-" + 1-18 '
            'letters or digits (at most 31 characters)')
    if synthetic_only(config) and not is_synthetic(hsa_id, config):
        raise QualificationRefused(
            400, 'hsa_id_not_synthetic',
            f'The pilot accepts synthetic HSA-ids only: the part after "-" must '
            f'start with {synthetic_prefix(config)} (record.pdhc D8)')
    return hsa_id


def normalise_legitimation(value):
    code = (value or '').strip().lower() if isinstance(value, str) else ''
    if not code:
        return None
    if code not in LEGITIMATIONSYRKEN:
        raise QualificationRefused(
            400, 'invalid_legitimation',
            'legitimation must be one of: ' + ', '.join(LEGITIMATIONSYRKEN))
    return code


def qualification_of(session, professional):
    if professional is None:
        return None
    return session.query(ProfessionalQualification).filter_by(
        professional_guid=professional.guid).first()


def for_blob(session, professional, config):
    """The four blob fields. Always all four, so a consumer never has to tell
    "absent" from "none recorded"."""
    q = qualification_of(session, professional)
    hsa_id = q.hsa_id if q else None
    return {
        'hsa_id': hsa_id,
        'hsa_id_synthetic': bool(hsa_id) and is_synthetic(hsa_id, config),
        'legitimation': q.legitimation if q else None,
        'legitimation_verified': bool(q.legitimation_verified) if q else False,
    }


def set_qualification(session, actor, target_user_guid, data, config, ip=None):
    """SU sets HSA-id and/or legitimation. `data` keys present are changed;
    a blank value clears. Returns the blob fields after the change."""
    if actor is None or not actor.is_su_admin:
        # Callers gate on SU already; this is the second lock, so a new caller
        # that forgets the decorator still cannot set them.
        raise QualificationRefused(403, 'forbidden', 'SU admin access required')
    target = session.query(User).filter_by(guid=(target_user_guid or '').strip()).first()
    prof = (session.query(Professional).filter_by(user_id=target.id).first()
            if target is not None else None)
    if prof is None:
        raise QualificationRefused(404, 'not_found', 'Professional not found')

    changes = {}
    if 'hsa_id' in data:
        changes['hsa_id'] = normalise_hsa_id(data.get('hsa_id'), config)
    if 'legitimation' in data:
        changes['legitimation'] = normalise_legitimation(data.get('legitimation'))
    if not changes:
        raise QualificationRefused(400, 'invalid_request',
                                   'Give hsa_id and/or legitimation')

    q = qualification_of(session, prof)
    if changes.get('hsa_id'):
        holder = session.query(ProfessionalQualification).filter_by(
            hsa_id=changes['hsa_id']).first()
        if holder is not None and holder.professional_guid != prof.guid:
            raise QualificationRefused(409, 'hsa_id_taken',
                                       'This HSA-id belongs to another professional')
    if q is None:
        q = ProfessionalQualification(professional_guid=prof.guid,
                                      legitimation_verified=False)
        session.add(q)

    diff = {}
    for field, new in changes.items():
        old = getattr(q, field)
        if old != new:
            diff[field] = {'from': old, 'to': new}
            setattr(q, field, new)
    if 'legitimation' in diff:
        q.legitimation_verified = False
    q.updated_by_user_guid = actor.guid
    try:
        session.flush()
    except IntegrityError:
        # Another SU gave the same HSA-id to someone else between the check
        # above and this write: the unique index decides, and it is a 409.
        session.rollback()
        raise QualificationRefused(409, 'hsa_id_taken',
                                   'This HSA-id belongs to another professional')
    audit('set_professional_qualification', user_guid=actor.guid,
          detail={'target_guid': target.guid, 'professional_guid': prof.guid,
                  'changes': diff}, ip=ip)
    return for_blob(session, prof, config)
