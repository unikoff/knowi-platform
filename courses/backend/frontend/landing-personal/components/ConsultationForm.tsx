import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowRight, ArrowUpRight, Check, ChevronDown, Phone, X } from 'lucide-react';
import { apiRequest } from '@platform/shared/api/client';
import { trackConsultationAccepted } from '../analytics';
import '../styles/consultation-form.css';

const telegramDraft = `Здравствуйте! Хочу записаться на консультацию по backend-менторству.
Можете обращаться ко мне XXXX.

Мне было бы удобно созвониться в XX:XX завтра. Получится?
Также хотел бы сказать, что только начинаю знакомство с программированием и хочу узнать, как проходит обучение`;
const telegramUrl = `https://t.me/unikoff?text=${encodeURIComponent(telegramDraft)}`;

export function ConsultationForm({ showActions = true, courseId, landingBaseUrl }: {
  showActions?: boolean; courseId: string; landingBaseUrl: string;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const requestRef = useRef<{ payload: string; key: string } | null>(null);
  const [showPhoneForm, setShowPhoneForm] = useState(false);
  const [name, setName] = useState('');
  const [contact, setContact] = useState('');
  const [message, setMessage] = useState('');
  const [pending, setPending] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState('');

  const open = () => {
    const dialog = dialogRef.current;
    if (!dialog || dialog.open) return;
    if (success) {
      setName(''); setContact(''); setMessage('');
      setSuccess(false); requestRef.current = null;
    }
    setShowPhoneForm(false);
    setError('');
    dialog.showModal();
  };

  useEffect(() => {
    const handleTrigger = (event: MouseEvent) => {
      const trigger = event.target instanceof Element ? event.target.closest('[data-consultation-trigger]') : null;
      if (trigger) open();
    };
    document.addEventListener('click', handleTrigger);
    return () => document.removeEventListener('click', handleTrigger);
  });

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const cleanedName = name.trim().replace(/\s+/g, ' ');
    const cleanedContact = contact.trim();
    if (cleanedName.length < 2) {
      setError('Пожалуйста, укажите имя: не менее двух символов.');
      return;
    }
    if (!/^[+\d\s()\-]+$/.test(cleanedContact) || cleanedContact.replace(/\D/g, '').length < 7) {
      setError('Пожалуйста, укажите корректный номер телефона.');
      return;
    }
    const body = { name: cleanedName, contact: cleanedContact, goal: message.trim(), consent: true, consent_version: 'consultation-v3' };
    const payload = JSON.stringify(body);
    if (!requestRef.current || requestRef.current.payload !== payload) {
      requestRef.current = { payload, key: crypto.randomUUID() };
    }
    setPending(true);
    setError('');
    try {
      const result = await apiRequest<{ accepted: boolean }>(`/landings/${courseId}/consultations`, body, 'POST', {
        'Idempotency-Key': requestRef.current.key,
      });
      if (!result.accepted) throw new Error('Заявка не принята');
      setSuccess(true);
      trackConsultationAccepted();
    } catch {
      setError('Не удалось отправить заявку. Проверьте соединение и попробуйте ещё раз.');
    } finally {
      setPending(false);
    }
  };

  return <>
    {showActions && <div className="consultation-actions">
      <button className="primary-action landing-action consultation-actions__primary" type="button" onClick={open}>Оставить заявку <ArrowRight size={17} aria-hidden="true" /></button>
    </div>}
    <dialog ref={dialogRef} className="consultation-dialog" aria-labelledby="consultation-title" onCancel={(event) => { if (pending) event.preventDefault(); }}>
      <div className="consultation-dialog__body">
        <button className="consultation-dialog__close" type="button" aria-label="Закрыть форму" disabled={pending} onClick={() => dialogRef.current?.close()}><X size={19} aria-hidden="true" /></button>
        {success ? <div className="consultation-success" role="status">
          <span className="consultation-success__icon"><Check size={29} aria-hidden="true" /></span>
          <h2 id="consultation-title">Заявка отправлена</h2>
          <p>Спасибо! Позвоню по указанному номеру, чтобы обсудить менторство.</p>
          <button className="primary-action landing-action consultation-form__submit" type="button" onClick={() => dialogRef.current?.close()}>Понятно <Check size={17} aria-hidden="true" /></button>
        </div> : <>
          <h2 id="consultation-title">Консультация</h2>
          <p className="consultation-dialog__lead">Выберите удобный способ связи.</p>
          <div className="consultation-dialog__channels">
            <a className="consultation-channel consultation-channel--telegram" href={telegramUrl} target="_blank" rel="noopener noreferrer">
              <span className="consultation-channel__icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.161-1.933 9.11c-.143.646-.525.803-1.06.5l-2.94-2.168-1.419 1.366c-.157.157-.29.29-.594.29l.213-3.054 5.559-5.023c.242-.214-.054-.333-.374-.119l-6.873 4.328-2.96-.924c-.644-.202-.656-.644.135-.953l11.57-4.46c.537-.194 1.006.128.832.941z" /></svg>
              </span>
              <ArrowUpRight className="consultation-channel__indicator" size={16} aria-hidden="true" />
              <span className="consultation-channel__label">Telegram</span>
              <small>Чат с черновиком</small>
            </a>
            <button
              className="consultation-channel"
              type="button"
              aria-expanded={showPhoneForm}
              aria-controls="consultation-phone-form"
              disabled={pending}
              onClick={() => setShowPhoneForm(!showPhoneForm)}
            >
              <span className="consultation-channel__icon"><Phone size={19} aria-hidden="true" /></span>
              <ChevronDown className="consultation-channel__indicator" size={17} aria-hidden="true" />
              <span className="consultation-channel__label" id="consultation-phone-label">Телефон</span>
              <small>Обратный звонок</small>
            </button>
          </div>
          <form className="consultation-form" id="consultation-phone-form" aria-labelledby="consultation-phone-label" hidden={!showPhoneForm} onSubmit={submit}>
            <div className="consultation-form__contact">
              <label className="consultation-form__field">Как к вам обращаться
                <input required minLength={2} maxLength={80} autoComplete="name" placeholder="Ваше имя" value={name} onChange={(event) => setName(event.target.value)} />
              </label>
              <label className="consultation-form__field">Номер телефона
                <input required type="tel" maxLength={120} autoComplete="tel" placeholder="+7 (999) 000-00-00" value={contact} onChange={(event) => setContact(event.target.value)} />
              </label>
            </div>
            <label className="consultation-form__field">
              <span className="consultation-form__label">Ваш вопрос <small>необязательно</small></span>
              <textarea rows={2} maxLength={500} placeholder="Напишите, что хотели бы обсудить" value={message} onChange={(event) => setMessage(event.target.value)} />
            </label>
            {error && <p className="consultation-form__error" role="alert">{error}</p>}
            <button className="primary-action landing-action consultation-form__submit" type="submit" disabled={pending}>{pending ? 'Отправляем…' : 'Заказать звонок'} <ArrowRight size={17} aria-hidden="true" /></button>
            <p className="consultation-form__consent">Нажимая «Заказать звонок», вы соглашаетесь на <a href={`${landingBaseUrl}/personal-data`} target="_blank" rel="noopener noreferrer">обработку персональных данных</a>.</p>
          </form>
        </>}
      </div>
    </dialog>
  </>;
}
