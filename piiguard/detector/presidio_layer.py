"""Extra detectors from Presidio (https://github.com/data-privacy-stack/presidio, MIT).

Presidio adds checksum- and context-aware recognizers on top of PII Guard's own rules:
international phone numbers (Google libphonenumber), Indian IDs (Aadhaar, PAN, passport,
voter ID, vehicle registration, GSTIN), cards, IBAN, crypto wallets and US SSN.

Offline by design:
  * the spaCy model (en_core_web_sm) ships with the app and is only used for
    tokens and lemmas that Presidio's context scoring needs;
  * Presidio's EmailRecognizer is not loaded because its tldextract dependency can
    download the public-suffix list (PII Guard has its own email rule);
  * names and places stay with the local BERT model, which is more accurate than
    the small spaCy model, so spaCy's own entities are not requested.
"""
from __future__ import annotations

import logging
from typing import List

from .types import Finding

log = logging.getLogger(__name__)

# Presidio entity -> PII Guard kind
ENTITY_TO_KIND = {
    "IN_AADHAAR": "AADHAAR",
    "IN_PAN": "PAN",
    "IN_PASSPORT": "PASSPORT",
    "IN_VOTER": "VOTER_ID",
    "IN_VEHICLE_REGISTRATION": "VEHICLE_REG",
    "IN_GSTIN": "GSTIN",
    "PHONE_NUMBER": "PHONE",
    "CREDIT_CARD": "CARD",
    "IBAN_CODE": "BANK_ACCOUNT",
    "CRYPTO": "CRYPTO_WALLET",
    "US_SSN": "SSN",
}

# Below these scores Presidio results are too weak to show (Presidio scores 0..1).
MIN_SCORE = {"PHONE": 0.4, "VEHICLE_REG": 0.5}
DEFAULT_MIN_SCORE = 0.5


class PresidioDetector:
    def __init__(self, spacy_model: str = "en_core_web_sm"):
        import spacy
        from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
        from presidio_analyzer.nlp_engine import SpacyNlpEngine
        from presidio_analyzer.predefined_recognizers import (
            CreditCardRecognizer, CryptoRecognizer, IbanRecognizer, InAadhaarRecognizer,
            InGstinRecognizer, InPanRecognizer, InPassportRecognizer, InVehicleRegistrationRecognizer,
            InVoterRecognizer, PhoneRecognizer, UsSsnRecognizer)

        if not spacy.util.is_package(spacy_model):
            # Never let Presidio fall back to downloading the model.
            raise RuntimeError(f"spaCy model {spacy_model} is not bundled")

        nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": spacy_model}])
        nlp_engine.load()
        registry = RecognizerRegistry(supported_languages=["en"])
        for rec in (
            InAadhaarRecognizer(), InPanRecognizer(), InPassportRecognizer(), InVoterRecognizer(),
            InVehicleRegistrationRecognizer(), InGstinRecognizer(),
            PhoneRecognizer(supported_regions=("IN", "US", "GB", "AE", "SG", "AU", "CA", "DE", "FR")),
            CreditCardRecognizer(), IbanRecognizer(), CryptoRecognizer(), UsSsnRecognizer(),
        ):
            registry.add_recognizer(rec)
        self.analyzer = AnalyzerEngine(registry=registry, nlp_engine=nlp_engine, supported_languages=["en"])
        self.entities = sorted(ENTITY_TO_KIND)
        self.model_name = f"Presidio + {spacy_model}"
        log.info("Presidio analyzer loaded (%d recognizers)", len(registry.recognizers))

    def detect(self, text: str) -> List[Finding]:
        out: List[Finding] = []
        for r in self.analyzer.analyze(text=text, language="en", entities=self.entities):
            kind = ENTITY_TO_KIND.get(r.entity_type)
            if kind is None or r.score < MIN_SCORE.get(kind, DEFAULT_MIN_SCORE):
                continue
            value = text[r.start:r.end]
            if not value.strip():
                continue
            out.append(Finding(r.start, r.end, kind, value, float(r.score), "presidio"))
        return out
