from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConfirmedFact:
    value: str
    source: str
    confidence: float = 1.0


@dataclass
class ResearchContext:
    first: str = ''
    last: str = ''
    birth_year: Optional[int] = None
    birth_place: str = ''
    death_year: Optional[int] = None
    death_place: str = ''
    generation: int = 1
    generation_label: str = 'target'

    confirmed_facts: dict[str, ConfirmedFact] = field(default_factory=dict)
    ruled_out: list[str] = field(default_factory=list)

    @property
    def constraints(self) -> dict:
        c = {}
        if self.birth_year:
            c['birth_year_min'] = self.birth_year - 5
            c['birth_year_max'] = self.birth_year + 5
        if self.birth_place:
            c['birth_place'] = self.birth_place.lower()
        if self.death_year:
            c['death_year_min'] = self.death_year - 3
            c['death_year_max'] = self.death_year + 3
        return c

    def confirm(self, field_name: str, value: str, source: str,
                confidence: float = 1.0):
        self.confirmed_facts[field_name] = ConfirmedFact(
            value=value, source=source, confidence=confidence
        )

    def rule_out(self, description: str, reason: str):
        self.ruled_out.append(f'{description} — {reason}')

    def score_result(self, result: dict) -> int:
        score = 50

        c = self.constraints
        result_by = result.get('birth_year')
        result_bp = (result.get('birth_place') or result.get('location') or '').lower()

        if 'birth_year_min' in c and result_by:
            try:
                ry = int(str(result_by)[:4])
                if c['birth_year_min'] <= ry <= c['birth_year_max']:
                    score += 30
                elif abs(ry - self.birth_year) <= 15:
                    score += 10
                else:
                    score -= 40
            except (ValueError, TypeError):
                pass

        if 'birth_place' in c and result_bp:
            place_tokens = set(c['birth_place'].replace(',', ' ').split())
            result_tokens = set(result_bp.replace(',', ' ').split())
            overlap = place_tokens & result_tokens
            if overlap:
                score += 20
            else:
                score -= 50

        last_lower = self.last.lower()
        title = (result.get('title') or result.get('name') or '').lower()
        if last_lower and last_lower in title:
            score += 10
        elif last_lower not in title and last_lower:
            score -= 10

        return max(0, min(100, score))

    def filter_results(self, results: list, min_score: int = 25) -> list:
        scored = []
        for r in results:
            r = dict(r)
            r['context_score'] = self.score_result(r)
            scored.append(r)
        return [r for r in scored if r['context_score'] >= min_score]

    def to_prompt_block(self) -> str:
        lines = [
            f'GENERATION: {self.generation} ({self.generation_label})',
            f'TARGET: {self.first} {self.last}',
        ]
        if self.birth_year:
            lines.append(f'BIRTH: ~{self.birth_year}'
                         + (f', {self.birth_place}' if self.birth_place else ''))
        if self.death_year:
            lines.append(f'DEATH: ~{self.death_year}'
                         + (f', {self.death_place}' if self.death_place else ''))

        c = self.constraints
        if c:
            lines.append('\nCONSTRAINTS (any valid result MUST satisfy these):')
            if 'birth_year_min' in c:
                lines.append(f'  - Birth year: {c["birth_year_min"]}–{c["birth_year_max"]}')
            if 'birth_place' in c:
                lines.append(f'  - Birth place contains: {c["birth_place"]}')

        if self.confirmed_facts:
            lines.append('\nCONFIRMED FACTS (with sources):')
            for k, f in self.confirmed_facts.items():
                lines.append(f'  - {k}: {f.value} [source: {f.source}]')

        if self.ruled_out:
            lines.append('\nRULED OUT:')
            for r in self.ruled_out:
                lines.append(f'  - {r}')

        return '\n'.join(lines)

    @classmethod
    def from_person(cls, person: dict, generation: int = 1,
                    generation_label: str = 'target') -> 'ResearchContext':
        return cls(
            first=person.get('first_name', '') or '',
            last=person.get('last_name', '') or '',
            birth_year=person.get('birth_year'),
            birth_place=person.get('birth_state') or person.get('birth_place') or '',
            death_year=person.get('death_year'),
            death_place=person.get('death_place') or '',
            generation=generation,
            generation_label=generation_label,
        )
