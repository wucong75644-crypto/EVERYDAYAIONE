import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { generateRequirementSuggestions } from '../services/ecomRequirement';
import { toApiRequestError } from '../services/api';
import type { DetailGenerationForm } from '../types/detailPage';
import type { RequirementAssistResult, RequirementRevision } from '../types/ecomRequirement';
import { buildSupplementText, draftValidationError, formatRequirementDraft, REQUIREMENT_MAX_LENGTH } from '../utils/requirementAssist';

type AssistStatus = 'idle' | 'loading' | 'success' | 'error';
interface RequestSource { projectId: string; form: DetailGenerationForm }

export function useDetailRequirementAssist() {
  const [isOpen, setIsOpen] = useState(false);
  const [status, setStatus] = useState<AssistStatus>('idle');
  const [draft, setDraft] = useState<RequirementAssistResult | null>(null);
  const [supplement, setSupplement] = useState('');
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [skippedQuestions, setSkippedQuestions] = useState<string[]>([]);
  const [history, setHistory] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<RequestSource | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const requestVersionRef = useRef(0);

  const requestDraft = useCallback(async (revision?: RequirementRevision, nextHistory?: string[]) => {
    const source = sourceRef.current;
    if (!source) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const requestVersion = ++requestVersionRef.current;
    setStatus('loading');
    setError(null);
    try {
      const response = await generateRequirementSuggestions(source.projectId, source.form, controller.signal, revision);
      if (controller.signal.aborted || requestVersion !== requestVersionRef.current) return;
      setDraft(response.data);
      if (nextHistory) setHistory(nextHistory);
      setAnswers({});
      setSupplement('');
      setStatus('success');
    } catch (requestError) {
      if (controller.signal.aborted || requestVersion !== requestVersionRef.current) return;
      setError(toApiRequestError(requestError).message);
      setStatus('error');
    } finally {
      if (requestVersion === requestVersionRef.current) controllerRef.current = null;
    }
  }, []);

  const open = useCallback(async (projectId: string, form: DetailGenerationForm) => {
    sourceRef.current = { projectId, form: { ...form } };
    setIsOpen(true);
    setDraft(null);
    setHistory([]);
    setAnswers({});
    setSkippedQuestions([]);
    setSupplement('');
    await requestDraft();
  }, [requestDraft]);

  const update = useCallback(async () => {
    if (!draft) return requestDraft();
    const issue = draftValidationError(draft);
    if (issue) { setError(issue); return; }
    const text = buildSupplementText(draft, answers, skippedQuestions, supplement);
    const nextHistory = text ? [...history, text] : history;
    const allSupplement = nextHistory.join('\n\n');
    if (allSupplement.length > 4000) { setError('补充内容超过4000字，请精简后再更新'); return; }
    await requestDraft({ draft, supplement: allSupplement, skipped_questions: skippedQuestions }, nextHistory);
  }, [draft, answers, skippedQuestions, supplement, history, requestDraft]);

  const close = useCallback(() => {
    requestVersionRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setIsOpen(false);
  }, []);

  const updateDraft = useCallback((patch: Partial<RequirementAssistResult>) => {
    setDraft(current => current ? { ...current, ...patch } : current);
  }, []);
  const answerQuestion = useCallback((question: string, value: string) => {
    setAnswers(current => ({ ...current, [question]: value }));
  }, []);
  const toggleSkip = useCallback((question: string) => {
    setSkippedQuestions(current => current.includes(question) ? current.filter(item => item !== question) : [...current, question].slice(-30));
  }, []);

  useEffect(() => () => {
    requestVersionRef.current += 1;
    controllerRef.current?.abort();
  }, []);

  const brief = useMemo(() => {
    if (!draft) return '';
    const text = buildSupplementText(draft, answers, skippedQuestions, supplement);
    return formatRequirementDraft(draft, sourceRef.current?.form.requirement ?? '', history, skippedQuestions, text);
  }, [draft, answers, skippedQuestions, supplement, history]);
  const validationError = draft ? draftValidationError(draft)
    ?? (brief.length > REQUIREMENT_MAX_LENGTH ? '内容超过10000字，请精简后再采用' : null) : null;

  return {
    isOpen, status, isLoading: status === 'loading', draft, brief, error, validationError,
    supplement, answers, skippedQuestions,
    sourceProjectId: sourceRef.current?.projectId,
    open, close, update, updateDraft, setSupplement, answerQuestion, toggleSkip,
  };
}
