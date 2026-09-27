"""Sarvam AI multilingual voice integration (STT, Translation, and TTS).

Provides a purely additive voice layer around the existing LiveAgent without
modifying any core agent or Sentinel-Z gateway decision logic:
  1. Speech-to-Text (`saaras:v3` / `saarika:v2.5`) detects the spoken language
     and transcribes the user's voice input.
  2. If the spoken language is non-English (e.g. `hi-IN`, `ta-IN`, `te-IN`),
     translates the prompt to English for the underlying tool-calling agent,
     while keeping the session's active voice language locked to the user's
     spoken language.
  3. Immediately synthesizes a "Working on it..." audio cue in the user's
     detected language (`bulbul:v3` / `bulbul:v2`).
  4. Once the agent completes the task, translates the final summary back into
     the user's language and synthesizes spoken audio of what was done.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

import httpx

from arena import config

logger = logging.getLogger(__name__)

#: Localized "Working on it" acknowledgments by BCP-47 language code.
WORKING_PHRASES: dict[str, str] = {
    "en-IN": "Working on it right now, please wait a moment.",
    "hi-IN": "जी, मैं इस पर काम कर रहा हूँ, कृपया थोड़ा इंतज़ार करें।",
    "bn-IN": "অবশ্যই, আমি এটি নিয়ে কাজ করছি, অনুগ্রহ করে একটু অপেক্ষা করুন।",
    "ta-IN": "நிச்சயமாக, நான் இதைச் செய்து கொண்டிருக்கிறேன், சற்று காத்திருங்கள்.",
    "te-IN": "తప్పకుండా, నేను దీనిపై పని చేస్తున్నాను, దయచేసి వేచి ఉండండి.",
    "kn-IN": "ಖಂಡಿತ, ನಾನು ಇದರ ಮೇಲೆ ಕೆಲಸ ಮಾಡುತ್ತಿದ್ದೇನೆ, ದಯವಿಟ್ಟು ನಿರೀಕ್ಷಿಸಿ.",
    "ml-IN": "തീർച്ചയായും, ഞാൻ ഇതിൽ പ്രവർത്തിക്കുകയാണ്, ദയവായി കാത്തിരിക്കുക.",
    "mr-IN": "नक्कीच, मी यावर काम करत आहे, कृपया थोडी वाट पहा.",
    "gu-IN": "ચોક્કસ, હું આના પર કામ કરી રહ્યો છું, કૃપા કરીને થોડી રાહ જુઓ.",
    "pa-IN": "ਜੀ ਹਾਂ, ਮੈਂ ਇਸ 'ਤੇ ਕੰਮ ਕਰ ਰਿਹਾ ਹਾਂ, ਕਿਰਪਾ ਕਰਕੇ ਥੋੜ੍ਹਾ ਇੰਤਜ਼ਾਰ ਕਰੋ।",
    "od-IN": "ନିଶ୍ଚୟ, ମୁଁ ଏହା ଉପରେ କାମ କରୁଛି, ଦୟାକରି ଟିକିଏ ଅପେକ୍ଷା କରନ୍ତୁ।",
    "or-IN": "ନିଶ୍ଚୟ, ମୁଁ ଏହା ଉପରେ କାମ କରୁଛି, ଦୟାକରି ଟିକିଏ ଅପେକ୍ଷା କରନ୍ତୁ।",
}

LANGUAGE_LABELS: dict[str, str] = {
    "en-IN": "English",
    "hi-IN": "Hindi (हिन्दी)",
    "bn-IN": "Bengali (বাংলা)",
    "ta-IN": "Tamil (தமிழ்)",
    "te-IN": "Telugu (తెలుగు)",
    "kn-IN": "Kannada (ಕನ್ನಡ)",
    "ml-IN": "Malayalam (മലയാളം)",
    "mr-IN": "Marathi (मराठी)",
    "gu-IN": "Gujarati (ગુજરાતી)",
    "pa-IN": "Punjabi (ਪੰਜਾਬੀ)",
    "od-IN": "Odia (ଓଡ଼ିଆ)",
}


def clean_for_speech(text: str) -> str:
    """Strip markdown symbols and raw URLs so TTS reads prose naturally."""
    cleaned = re.sub(r"https?://\S+", "", text)
    cleaned = re.sub(r"[*_`#~>|]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:900] if len(cleaned) > 900 else cleaned


def normalize_lang_code(code: str | None) -> str:
    """Ensure a valid Sarvam BCP-47 language code (defaults to en-IN)."""
    if not code:
        return "en-IN"
    clean = code.strip()
    if clean in ("auto", "unknown", ""):
        return "en-IN"
    if clean == "or-IN":
        return "od-IN"
    if "-" not in clean and len(clean) == 2:
        return f"{clean.lower()}-IN"
    return clean


_HINGLISH_MARKERS: frozenset[str] = frozenset(
    {
        "karo",
        "kijiye",
        "khabar",
        "taza",
        "taaza",
        "mera",
        "meri",
        "mere",
        "mujhe",
        "mujhko",
        "kya",
        "hai",
        "hain",
        "batao",
        "bataiye",
        "dikhao",
        "dhundo",
        "khojo",
        "bhejo",
        "padho",
        "khareedo",
        "daalo",
        "dalo",
        "aur",
        "mein",
        "par",
        "se",
        "ko",
        "ka",
        "ki",
        "ke",
        "sabhi",
        "naya",
        "nayi",
        "samachar",
        "kal",
        "aaj",
        "abhi",
        "zaroor",
        "kripya",
        "dhanyawad",
        "shukriya",
        "wala",
        "wali",
        "wale",
        "chahiye",
        "dekho",
    }
)


def _looks_like_hinglish(text: str) -> bool:
    """Detect Roman-script Hindi (Hinglish) when STT mislabels it as en-IN."""
    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    hits = words & _HINGLISH_MARKERS
    return len(hits) >= 2 or bool(
        hits & {"karo", "khabar", "taza", "batao", "dikhao", "dhundo", "mujhe", "chahiye"}
    )


def _has_indic_script(text: str) -> bool:
    """Return True if text contains characters from any Indian Unicode block."""
    return any("\u0900" <= ch <= "\u0d7f" for ch in text)


class SarvamVoiceService:
    """Client for Sarvam AI Speech-to-Text, Translation, and Text-to-Speech."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = (api_key if api_key is not None else config.SARVAM_API_KEY).strip()
        self.base_url = (base_url or config.SARVAM_BASE_URL).rstrip("/")
        self.speaker = config.SARVAM_SPEAKER or "shubh"

    def _refresh_env_if_needed(self) -> None:
        if self.api_key:
            return
        try:
            from dotenv import dotenv_values

            vals = dotenv_values(config.REPO_ROOT / ".env")
            env_key = (vals.get("SARVAM_API_KEY") or "").strip()
            if env_key:
                self.api_key = env_key
            env_speaker = (vals.get("SARVAM_SPEAKER") or "").strip()
            if env_speaker:
                self.speaker = env_speaker
        except Exception:
            pass

    @property
    def configured(self) -> bool:
        self._refresh_env_if_needed()
        return bool(self.api_key)

    def set_api_key(self, api_key: str) -> None:
        self.api_key = api_key.strip()

    def _headers(self) -> dict[str, str]:
        self._refresh_env_if_needed()
        return {"api-subscription-key": self.api_key}

    # --------------------------------------------------------- Speech-to-Text
    def speech_to_text(self, audio_b64: str, language_hint: str = "auto") -> dict[str, Any]:
        """Transcribe base64 WAV audio, detect language, translate to English for the
        system, and synthesize the localized 'Working on it' audio response."""
        if not self.configured:
            return {
                "ok": False,
                "error": "SARVAM_API_KEY is not configured. Add it to .env or paste it in Settings.",
            }

        raw_b64 = audio_b64.split(",", 1)[-1] if "," in audio_b64 else audio_b64
        try:
            audio_bytes = base64.b64decode(raw_b64)
        except Exception as exc:
            return {"ok": False, "error": f"Invalid audio data: {exc}"}

        lang_param = (
            "unknown"
            if not language_hint or language_hint in ("auto", "unknown")
            else normalize_lang_code(language_hint)
        )

        transcript = ""
        english_text = ""
        detected_lang = "en-IN" if lang_param == "unknown" else lang_param
        last_err = ""

        # Step 1: Use saaras:v3 mode="translate" with language_code="unknown" (or hint)
        # so Sarvam directly detects the spoken language AND translates speech -> English.
        try:
            resp_tr = httpx.post(
                f"{self.base_url}/speech-to-text",
                headers=self._headers(),
                files={"file": ("speech.wav", audio_bytes, "audio/wav")},
                data={"model": "saaras:v3", "mode": "translate", "language_code": lang_param},
                timeout=20.0,
            )
            if resp_tr.status_code == 200:
                payload_tr = resp_tr.json()
                english_text = str(payload_tr.get("transcript") or "").strip()
                raw_lang = payload_tr.get("language_code")
                if raw_lang and raw_lang not in ("unknown", "auto"):
                    detected_lang = normalize_lang_code(str(raw_lang))
            else:
                last_err = f"HTTP {resp_tr.status_code}: {resp_tr.text[:200]}"
        except Exception as exc:
            last_err = str(exc)

        # Step 2: Also get the native-language transcript for UI display (or fallback STT)
        transcribe_lang = detected_lang if detected_lang != "en-IN" else lang_param
        for form_data in (
            {"model": "saaras:v3", "mode": "transcribe", "language_code": transcribe_lang},
            {"model": "saarika:v2.5", "language_code": transcribe_lang},
        ):
            try:
                resp = httpx.post(
                    f"{self.base_url}/speech-to-text",
                    headers=self._headers(),
                    files={"file": ("speech.wav", audio_bytes, "audio/wav")},
                    data=form_data,
                    timeout=20.0,
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    t_cand = str(payload.get("transcript") or "").strip()
                    if t_cand:
                        transcript = t_cand
                    raw_lang2 = payload.get("language_code")
                    if (
                        detected_lang == "en-IN"
                        and raw_lang2
                        and raw_lang2 not in ("unknown", "auto", "en-IN")
                    ):
                        detected_lang = normalize_lang_code(str(raw_lang2))
                    break
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as exc:
                last_err = str(exc)

        if not transcript and english_text:
            transcript = english_text
        if not transcript:
            return {
                "ok": False,
                "error": f"Could not transcribe speech ({last_err or 'empty transcript'}).",
            }

        # Step 3: Hinglish / Romanized Hindi detection if STT reported en-IN
        if detected_lang.startswith("en") and (
            _looks_like_hinglish(transcript) or _looks_like_hinglish(english_text)
        ):
            detected_lang = "hi-IN"

        # Step 4: Ensure `english_text` is genuinely in English before sending to the agent
        if not english_text or _looks_like_hinglish(english_text) or _has_indic_script(english_text):
            src_for_tr = detected_lang if not detected_lang.startswith("en") else "hi-IN"
            translated = self.translate_text(
                transcript, source_lang=src_for_tr, target_lang="en-IN"
            )
            if translated:
                english_text = translated

        # If user spoke in Hindi/Indian language but transcript came back in English from translate,
        # keep transcript in the user's language if needed
        if not detected_lang.startswith("en") and transcript.strip().lower() == english_text.strip().lower():
            native_disp = self.translate_text(
                english_text, source_lang="en-IN", target_lang=detected_lang
            )
            if native_disp:
                transcript = native_disp

        # Step 5: Prepare localized "Working on it..." acknowledgment in the user's language
        working_text = WORKING_PHRASES.get(detected_lang)
        if not working_text:
            working_text = self.translate_text(
                WORKING_PHRASES["en-IN"], source_lang="en-IN", target_lang=detected_lang
            )
        working_audio_b64 = self.text_to_speech(working_text, target_lang=detected_lang)

        return {
            "ok": True,
            "transcript": transcript,
            "english_text": english_text or transcript,
            "language_code": detected_lang,
            "language_label": LANGUAGE_LABELS.get(detected_lang, detected_lang),
            "working_text": working_text,
            "working_audio_b64": working_audio_b64,
        }

    # ------------------------------------------------------------ Translation
    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text between languages using Sarvam Translate."""
        cleaned = clean_for_speech(text)
        if not cleaned or not self.configured:
            return cleaned
        src = normalize_lang_code(source_lang)
        tgt = normalize_lang_code(target_lang)
        if src == tgt:
            return cleaned

        for model_name in ("sarvam-translate:v1", "mayura:v1"):
            try:
                resp = httpx.post(
                    f"{self.base_url}/translate",
                    headers=self._headers(),
                    json={
                        "input": cleaned[:1800],
                        "source_language_code": src,
                        "target_language_code": tgt,
                        "model": model_name,
                    },
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    translated = str(resp.json().get("translated_text") or "").strip()
                    if translated:
                        return translated
            except Exception as exc:
                logger.warning("Sarvam translate (%s) failed: %s", model_name, exc)
        return cleaned

    # --------------------------------------------------------- Text-to-Speech
    def text_to_speech(self, text: str, target_lang: str = "hi-IN") -> str:
        """Synthesize speech in `target_lang` and return base64-encoded audio."""
        cleaned = clean_for_speech(text)
        if not cleaned or not self.configured:
            return ""
        tgt = normalize_lang_code(target_lang)

        # Try Bulbul v3 first, then fallback to Bulbul v2
        tts_payloads: list[dict[str, Any]] = [
            {
                "text": cleaned[:2000],
                "target_language_code": tgt,
                "speaker": self.speaker,
                "model": "bulbul:v3",
                "pace": 1.05,
            },
            {
                "inputs": [cleaned[:1400]],
                "target_language_code": tgt,
                "speaker": "anushka",
                "model": "bulbul:v2",
                "pace": 1.05,
            },
        ]
        for body in tts_payloads:
            try:
                resp = httpx.post(
                    f"{self.base_url}/text-to-speech",
                    headers=self._headers(),
                    json=body,
                    timeout=20.0,
                )
                if resp.status_code == 200:
                    audios = resp.json().get("audios") or []
                    if audios and isinstance(audios[0], str):
                        return audios[0]
            except Exception as exc:
                logger.warning("Sarvam TTS (%s) failed: %s", body.get("model"), exc)
        return ""

    # --------------------------------------------------- Final Turn Completion
    def prepare_voice_completion(self, english_reply: str, target_lang: str) -> dict[str, Any]:
        """Translate the agent's final response into `target_lang` and generate TTS audio."""
        tgt = normalize_lang_code(target_lang)
        spoken_en = clean_for_speech(english_reply)
        if tgt.startswith("en"):
            localized_text = spoken_en
        else:
            localized_text = self.translate_text(spoken_en, source_lang="en-IN", target_lang=tgt)

        audio_b64 = self.text_to_speech(localized_text, target_lang=tgt)
        return {
            "language_code": tgt,
            "language_label": LANGUAGE_LABELS.get(tgt, tgt),
            "localized_text": localized_text,
            "audio_b64": audio_b64,
        }


VOICE_SERVICE = SarvamVoiceService()
