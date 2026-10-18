from collections import defaultdict
from collections.abc import Sequence

from app.core.errors import ConflictError, DomainValidationError
from app.models import AnswerOption, Question, QuestionType, UserAnswer
from app.repositories.interfaces import InterestRepository, SurveyRepository
from app.schemas.survey import (
    AnswerOptionOut,
    FreeAnswerOut,
    InterestDirectionOut,
    InterestOut,
    MatchingProfile,
    QuestionOut,
    SurveyAnswer,
    SurveyPublicProfile,
)
from app.schemas.user import MeOut
from app.services.identity import IdentityService


class SurveyService:
    def __init__(
        self, surveys: SurveyRepository, interests: InterestRepository, identity: IdentityService
    ) -> None:
        self._surveys = surveys
        self._interests = interests
        self._identity = identity

    async def get_survey(self) -> list[QuestionOut]:
        questions = await self._surveys.list_active_questions()
        options = await self._surveys.list_options([q.id for q in questions])
        by_question: dict[int, list[AnswerOptionOut]] = defaultdict(list)
        for option in sorted(options, key=lambda o: (o.order, o.id)):
            by_question[option.question_id].append(AnswerOptionOut.model_validate(option))
        return [
            QuestionOut(id=q.id, text=q.text, type=q.type, order=q.order, options=by_question[q.id])
            for q in sorted(questions, key=lambda q: (q.order, q.id))
        ]

    async def get_answers(self, user_id: int) -> list[SurveyAnswer]:
        questions = await self._surveys.list_active_questions()
        rows = await self._surveys.list_active_answers(user_id)
        by_question: dict[int, SurveyAnswer] = {}
        for row in rows:
            answer = by_question.setdefault(
                row.question_id, SurveyAnswer(question_id=row.question_id)
            )
            if row.option_id is not None:
                if answer.option_ids is None:
                    answer.option_ids = []
                answer.option_ids.append(row.option_id)
            else:
                answer.text_value = row.text_value
        for answer in by_question.values():
            if answer.option_ids is not None:
                answer.option_ids.sort()
        return [
            by_question[q.id]
            for q in sorted(questions, key=lambda q: (q.order, q.id))
            if q.id in by_question
        ]

    async def submit_answers(self, user_id: int, answers: list[SurveyAnswer]) -> MeOut:
        await self._identity.lock_for_survey(user_id)
        questions = {q.id: q for q in await self._surveys.list_active_questions()}
        if not any(q.type != QuestionType.TEXT for q in questions.values()):
            raise ConflictError("survey_not_configured", "Анкета пока не настроена")
        options = {o.id: o for o in await self._surveys.list_options(list(questions))}
        approved = {i.id: i for i in await self._interests.list_approved()}
        leaves = {
            i.id
            for i in approved.values()
            if i.parent_id in approved and approved[i.parent_id].parent_id is None
        }
        rows: list[UserAnswer] = []
        interest_ids: set[int] = set()
        seen: set[int] = set()
        for answer in answers:
            question = questions.get(answer.question_id)
            if question is None:
                raise DomainValidationError("unknown_question", "Вопрос недоступен")
            if question.id in seen:
                raise DomainValidationError("duplicate_question", "Вопрос передан несколько раз")
            seen.add(question.id)
            chosen = self._validate_answer(question, answer, options)
            for option in chosen:
                if option.interest_id is not None:
                    if option.interest_id not in leaves:
                        raise ConflictError("survey_not_configured", "Анкета пока не настроена")
                    interest_ids.add(option.interest_id)
                rows.append(
                    UserAnswer(
                        user_id=user_id,
                        question_id=question.id,
                        option_id=option.id,
                        text_value=None,
                    )
                )
            if question.type == QuestionType.TEXT:
                rows.append(
                    UserAnswer(
                        user_id=user_id,
                        question_id=question.id,
                        option_id=None,
                        text_value=(answer.text_value or "").strip(),
                    )
                )
        if any(q.id not in seen and q.type != QuestionType.TEXT for q in questions.values()):
            raise DomainValidationError(
                "required_question_missing", "Ответьте на обязательные вопросы"
            )
        await self._surveys.replace_active_answers(user_id, list(questions), rows)
        await self._interests.replace_user_interests(user_id, interest_ids)
        return await self._identity.mark_survey_completed(user_id)

    @staticmethod
    def _validate_answer(
        question: Question, answer: SurveyAnswer, options: dict[int, AnswerOption]
    ) -> list[AnswerOption]:
        if question.type == QuestionType.TEXT:
            if answer.option_ids is not None:
                raise DomainValidationError("invalid_answer_type", "Для вопроса нужен текст")
            if "\x00" in (answer.text_value or ""):
                raise DomainValidationError(
                    "validation_error", "Текст содержит недопустимый символ"
                )
            if answer.text_value is not None and len(answer.text_value) > 300:
                raise DomainValidationError(
                    "text_too_long", "Ответ не должен превышать 300 символов"
                )
            if not (answer.text_value or "").strip():
                raise DomainValidationError("empty_text", "Текст ответа не должен быть пустым")
            return []
        if answer.text_value is not None:
            raise DomainValidationError("invalid_answer_type", "Выберите варианты ответа")
        ids = answer.option_ids or []
        if len(ids) != len(set(ids)):
            raise DomainValidationError("duplicate_option", "Вариант передан несколько раз")
        if question.type == QuestionType.SINGLE and len(ids) != 1:
            raise DomainValidationError("single_choice_violation", "Выберите ровно один вариант")
        if question.type == QuestionType.MULTI and not ids:
            raise DomainValidationError("multi_choice_violation", "Выберите хотя бы один вариант")
        if any(i not in options or options[i].question_id != question.id for i in ids):
            raise DomainValidationError("option_not_in_question", "Вариант не относится к вопросу")
        return [options[i] for i in ids]

    async def list_interests(self) -> list[InterestDirectionOut]:
        interests = sorted(await self._interests.list_approved(), key=lambda i: (i.name, i.id))
        return [
            InterestDirectionOut(
                id=root.id,
                name=root.name,
                children=[
                    InterestOut.model_validate(i) for i in interests if i.parent_id == root.id
                ],
            )
            for root in interests
            if root.parent_id is None
        ]

    async def get_matching_profile(self, user_ids: Sequence[int]) -> dict[int, MatchingProfile]:
        if not user_ids:
            return {}
        locations = await self._identity.get_locations(user_ids)
        interests = await self._interests.get_user_interest_ids(list(locations))
        return {
            user_id: MatchingProfile(
                interest_ids=interests.get(user_id, set()), office_id=office_id, city=city
            )
            for user_id, (office_id, city) in locations.items()
        }

    async def list_matching_candidate_ids(
        self, profile: MatchingProfile, excluded_user_ids: set[int], limit: int
    ) -> Sequence[int]:
        return await self._surveys.list_matching_candidate_ids(
            profile.interest_ids, profile.office_id, excluded_user_ids, limit
        )

    async def get_user_interests(self, user_ids: Sequence[int]) -> dict[int, list[InterestOut]]:
        if not user_ids:
            return {}
        interest_ids = await self._interests.get_user_interest_ids(user_ids)
        interests = sorted(await self._interests.list_approved(), key=lambda i: (i.name, i.id))
        return {
            user_id: [
                InterestOut.model_validate(i)
                for i in interests
                if i.id in interest_ids.get(user_id, set())
            ]
            for user_id in user_ids
        }

    async def get_free_answers(self, user_ids: Sequence[int]) -> dict[int, list[FreeAnswerOut]]:
        if not user_ids:
            return {}
        questions = {q.id: q for q in await self._surveys.list_active_questions()}
        answers: dict[int, list[FreeAnswerOut]] = defaultdict(list)
        for row in await self._surveys.list_active_text_answers(user_ids):
            question = questions.get(row.question_id)
            # A question can be deactivated between the two reads under READ COMMITTED.
            if question is None or question.type != QuestionType.TEXT or row.text_value is None:
                continue
            answers[row.user_id].append(
                FreeAnswerOut(
                    question_id=question.id,
                    question_text=question.text,
                    text_value=row.text_value,
                )
            )
        return {
            user_id: sorted(
                answers[user_id], key=lambda a: (questions[a.question_id].order, a.question_id)
            )
            for user_id in user_ids
        }

    async def get_public_profiles(self, user_ids: Sequence[int]) -> dict[int, SurveyPublicProfile]:
        interests = await self.get_user_interests(user_ids)
        answers = await self.get_free_answers(user_ids)
        return {
            user_id: SurveyPublicProfile(
                interests=interests[user_id], free_answers=answers[user_id]
            )
            for user_id in user_ids
        }
