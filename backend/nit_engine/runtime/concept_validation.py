"""Conservative model-output gate for source-linked concept graphs.

Never turns an LLM suggestion into a factual claim or an authorized source.
The calling service supplies evidence IDs from verified_coaching.verify(), never
from LLM output. All relations must refer to concepts in the same response.
"""
import json

class InvalidConceptMap(ValueError):
    pass


def validate_concept_map(raw, allowed_evidence_ids):
    """Validate exact concepts / relations / uncertainties JSON contract."""
    if isinstance(raw, str):
        try: data = json.loads(raw)
        except (ValueError, TypeError) as exc: raise InvalidConceptMap('invalid_json') from exc
    else: data = raw
    if not isinstance(data, dict) or set(data) != {'concepts','relations','uncertainties'}:
        raise InvalidConceptMap('schema_mismatch')
    concepts, relations, uncertainties = data['concepts'], data['relations'], data['uncertainties']
    if not isinstance(concepts,list) or len(concepts)>12 or not isinstance(relations,list) or len(relations)>20:
        raise InvalidConceptMap('invalid_graph_size')
    if not isinstance(uncertainties,list) or any(not isinstance(v,str) or len(v)>300 for v in uncertainties):
        raise InvalidConceptMap('invalid_uncertainties')
    allowed=set(allowed_evidence_ids)
    names=set()
    for concept in concepts:
        if not isinstance(concept,dict) or set(concept) != {'name','description','evidence_ids'}:
            raise InvalidConceptMap('invalid_concept_schema')
        name=concept['name']
        if not isinstance(name,str) or not name.strip() or len(name)>120 or name in names:
            raise InvalidConceptMap('duplicate_or_invalid_concept')
        names.add(name)
        if not isinstance(concept['description'],str) or not concept['description'].strip() or len(concept['description'])>1000:
            raise InvalidConceptMap('invalid_concept_description')
        _check_ids(concept['evidence_ids'],allowed)
    for relation in relations:
        if not isinstance(relation,dict) or set(relation) != {'from','to','relation','evidence_ids'}:
            raise InvalidConceptMap('invalid_relation_schema')
        if relation['from'] not in names or relation['to'] not in names:
            raise InvalidConceptMap('unregistered_relation_endpoint')
        if not isinstance(relation['relation'],str) or not relation['relation'].strip() or len(relation['relation'])>500:
            raise InvalidConceptMap('invalid_relation_label')
        _check_ids(relation['evidence_ids'],allowed)
    return data


def _check_ids(ids, allowed):
    if not isinstance(ids,list) or not ids or len(ids)>5 or len(set(map(str,ids)))!=len(ids):
        raise InvalidConceptMap('missing_or_invalid_evidence')
    if any(not isinstance(i,str) or i not in allowed for i in ids):
        raise InvalidConceptMap('unapproved_evidence_id')
