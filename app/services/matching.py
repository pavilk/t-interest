from app.core.errors import NotFoundError
from app.repositories.interfaces import MatchRepository
from app.schemas.matching import PersonRecommendationOut
from app.schemas.survey import MatchingProfile
from app.schemas.user import PublicProfile
from app.services.identity import IdentityService
from app.services.survey import SurveyService

OFFICE_BONUS = 0.15
CITY_BONUS = 0.05
CANDIDATE_LIMIT = 500


def jaccard(a: set[int], b: set[int]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def score(a: MatchingProfile, b: MatchingProfile) -> float:
    bonus = (
        OFFICE_BONUS if a.office_id == b.office_id else (CITY_BONUS if a.city == b.city else 0.0)
    )
    return round(jaccard(a.interest_ids, b.interest_ids) + bonus, 3)


class MatchingService:
    def __init__(
        self, matches: MatchRepository, survey: SurveyService, identity: IdentityService
    ) -> None:
        self._matches = matches
        self._survey = survey
        self._identity = identity

    async def recommend_people(self, user_id: int, limit: int) -> list[PersonRecommendationOut]:
        excluded = await self._matches.excluded_user_ids(user_id)
        own = (await self._survey.get_matching_profile([user_id])).get(user_id)
        if own is None:
            raise NotFoundError("user_not_found", "Пользователь не найден")
        candidate_ids = await self._survey.list_matching_candidate_ids(
            own, excluded, CANDIDATE_LIMIT
        )
        if not candidate_ids:
            return []
        # Capture the fields used in both ranking and the response once. Later
        # profile updates must not mix a score with a different set of interests/office.
        users = await self._identity.get_public_profiles(candidate_ids)
        interests = await self._survey.get_user_interests(list(users))
        profiles = {
            candidate_id: MatchingProfile(
                interest_ids={i.id for i in interests[candidate_id]},
                office_id=user.office.id,
                city=user.office.city,
            )
            for candidate_id, user in users.items()
        }
        ranked = sorted(
            ((candidate_id, score(own, profile)) for candidate_id, profile in profiles.items()),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
        selected_ids = [candidate_id for candidate_id, _ in ranked]
        free_answers = await self._survey.get_free_answers(selected_ids)
        return [
            PersonRecommendationOut(
                user=PublicProfile(
                    id=users[candidate_id].id,
                    name=users[candidate_id].name,
                    about=users[candidate_id].about,
                    office=users[candidate_id].office,
                    interests=interests[candidate_id],
                    free_answers=free_answers[candidate_id],
                ),
                score=value,
                common_interests=[i for i in interests[candidate_id] if i.id in own.interest_ids],
                same_office=profiles[candidate_id].office_id == own.office_id,
            )
            for candidate_id, value in ranked
        ]
