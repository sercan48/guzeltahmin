"""LLM Integration for Premium Telegram Commentary and Match Vetoing."""
import os
import json
import logging
from typing import Dict, Any

try:
    import google.generativeai as genai
except ImportError:
    genai = None

from config.settings import load_dotenv

logger = logging.getLogger("llm_client")

class SportsAnalystLLM:
    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self.is_configured = False
        
        if genai and self.api_key:
            try:
                genai.configure(api_key=self.api_key)
                # Using Gemini 1.5 Flash or Pro
                self.model = genai.GenerativeModel('gemini-1.5-flash')
                self.is_configured = True
            except Exception as e:
                logger.error(f"Failed to configure Gemini: {e}")
        else:
            logger.warning("Gemini API key missing or package not installed. LLM features disabled.")

    def analyze_picks_and_veto(self, coupon_name: str, picks_data: list) -> Dict[str, Any]:
        """
        Sends the mathematical predictions to LLM.
        Returns a dict containing {"telegram_message": str, "vetoed_matches": [str], "approved_matches": [str]}
        """
        if not self.is_configured:
            # Fallback if no AI setup.
            return {
                "telegram_message": "⚠️ LLM devre dışı (GEMINI_API_KEY eksik). Klasik XGBoost formatı aktif.\n\n" + str([p.get('match') for p in picks_data]),
                "vetoed_matches": [],
                "approved_matches": [p['match'] for p in picks_data]
            }

        prompt = f"""
Sen "Güzel Tahmin" adında, telegram'da çok saygın, elit ve paralı bir vip bahis grubunun baş spor analistisin (Tipster). 
İsabet oranın %90. Her yazdığın gönderi güven verir, tamamen verilere ve mantığa dayanır. 

Bizim XGBoost makine öğrenmesi algoritmamız bize bu hafta için [{coupon_name}] stratejisinde aşağıdaki maç tahminlerini (picks) getirdi. 
Ancak makine öğrenimi bazen takımlardaki kaosları, sakatlıkları veya oranı düşük diye banko sanılan sürprizleri (Değer bahsi olmayanları) bilemeyebilir.

GÖREVİN:
1. Bu maçları tek tek incele. İnsan sağduyusuyla gerçekten mantıklı olanlara **ONAY** ver. 
2. Tehlikeli riskler taşıyan, derbi stresi barındıran veya formsuz bir favorinin olduğu maçları **VETO ET** (kupondan çıkart).
3. Seçtiğin onaylı maçlar ile son derece estetik, bol emojili, premium telegram abonelerine gönderilmeye hazır, "Kuponumuz onaylandı" hissiyatını veren mükemmel bir Analiz & Kupon Gönderisi (Telegram Message) oluştur. Metnin başına Veto edilen maçlar varsa "(Sistem tarafından 1 maç riskli bulunup elendi!)" notu düş.
4. Bu işlemin sonucunu sadece JSON formatında dön. Başka metin yazma. 

Format:
{{
  "vetoed_matches": ["Takım A vs Takım B", "Takım C"],
  "approved_matches": ["Takım X vs Takım Y", "Takım Z"],
  "telegram_message": "Senin yazacağın harika emojili premium metin. Her maçın istatistiği ve Neden bu bahsi güvendiğine dair kısa analizinle dolu."
}}

Gelen XGBoost Tahmin Verisi:
{json.dumps(picks_data, ensure_ascii=False, indent=2)}

Sadece JSON dön. Asla Markdown (```json) bloğu içine alma. Süslü küme parantezi ile başla ve bitir.
"""

        try:
            # Enforce JSON adherence using response generation config if possible, 
            # but standard prompt is usually fine for Gemini 1.5.
            response = self.model.generate_content(prompt)
            raw_text = response.text.strip()
            # Clean possible markdown enclosures
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
                
            result = json.loads(raw_text.strip())
            return result
            
        except Exception as e:
            logger.error(f"LLM Veto analysis failed: {e}")
            return {
                "telegram_message": f"🤖 Yapay Zeka analiz yaparken hata oluştu (Fall-back aktif).\n{e}",
                "vetoed_matches": [],
                "approved_matches": [p['match'] for p in picks_data]
            }
