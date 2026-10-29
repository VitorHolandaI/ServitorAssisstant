"""O slim promete linguagem natural; o cliente CalDAV exige um formato só.

`nextcloud_add_task` anuncia "due_at is natural language, e.g. 'tomorrow at
6 pm'" e repassava direto para `_parse_user_datetime`, que aceita apenas
`%Y-%m-%d %H:%M:%S`. Um modelo pequeno lê a descrição, manda "amanhã às 9"
e toma erro. Estes testes fixam a tradução que faltava.
"""

import datetime
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_module.nextcloud_slim.stream import _to_iso_due  # noqa: E402
from mcp_module.stremable_http.nextcloud_tasks import NextcloudError  # noqa: E402

RECIFE = ZoneInfo("America/Recife")
AGORA = datetime.datetime(2026, 10, 29, 15, 0, 0, tzinfo=RECIFE)


class ToIsoDueTest(unittest.TestCase):
    def test_iso_completo_passa_intacto(self):
        self.assertEqual(
            _to_iso_due("2026-10-29 21:00:00", AGORA, RECIFE), "2026-10-29 21:00:00"
        )

    def test_iso_sem_segundos_ganha_zero(self):
        # O proprio assistente escreveu "2026-10-29 21:00" e foi recusado.
        self.assertEqual(
            _to_iso_due("2026-10-29 21:00", AGORA, RECIFE), "2026-10-29 21:00:00"
        )

    def test_portugues_relativo(self):
        self.assertEqual(
            _to_iso_due("amanhã às 9", AGORA, RECIFE), "2026-10-30 09:00:00"
        )

    def test_ingles_do_exemplo_da_docstring(self):
        # O exemplo que a descricao da tool mostra ao modelo precisa funcionar.
        self.assertEqual(
            _to_iso_due("tomorrow at 6 pm", AGORA, RECIFE), "2026-10-30 18:00:00"
        )

    def test_hora_sem_dia_cai_no_futuro(self):
        # 09:00 ja passou as 15:00, entao e amanha, nao hoje de manha.
        self.assertEqual(_to_iso_due("às 9", AGORA, RECIFE), "2026-10-30 09:00:00")

    def test_hora_com_h_e_minutos(self):
        self.assertEqual(
            _to_iso_due("amanhã às 9h30", AGORA, RECIFE), "2026-10-30 09:30:00"
        )

    def test_periodo_do_dia_vira_24h(self):
        self.assertEqual(
            _to_iso_due("amanhã às 9 da noite", AGORA, RECIFE), "2026-10-30 21:00:00"
        )

    def test_valor_impossivel_cita_o_valor_e_o_formato(self):
        with self.assertRaises(NextcloudError) as caught:
            _to_iso_due("banana com farofa", AGORA, RECIFE)
        mensagem = str(caught.exception)
        self.assertIn("banana com farofa", mensagem)
        self.assertIn("2026-08-05 18:00:00", mensagem)

    def test_vazio_e_recusado(self):
        with self.assertRaises(NextcloudError):
            _to_iso_due("   ", AGORA, RECIFE)


if __name__ == "__main__":
    unittest.main()
