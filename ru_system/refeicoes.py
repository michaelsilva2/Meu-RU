"""
refeicoes.py — regra de negócio compartilhada de registro de refeição.

Usada pelo autoatendimento do aluno, pelo registro manual do admin e pela
validação de QR code na entrada — todas debitam crédito e checam saldo da
mesma forma. `ignorar_limite_diario` existe porque o registro manual do
admin sempre pôde corrigir/forçar um lançamento além do limite de 2/dia;
QR e autoatendimento preservam esse limite.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from config import PRECOS_REFEICAO
from models import Aluno, HistoricoRefeicao, TipoRefeicao


class RefeicaoError(Exception):
    def __init__(self, motivo: str):
        self.motivo = motivo
        super().__init__(motivo)


def registrar_refeicao(
    db: Session,
    aluno: Aluno,
    tipo_enum: TipoRefeicao,
    admin_id: int | None = None,
    ignorar_limite_diario: bool = False,
) -> HistoricoRefeicao:
    if not ignorar_limite_diario:
        hoje_inicio = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        refeicoes_hoje = db.query(HistoricoRefeicao).filter(
            HistoricoRefeicao.aluno_id == aluno.id,
            HistoricoRefeicao.data_hora >= hoje_inicio,
        ).count()
        if refeicoes_hoje >= 2:
            raise RefeicaoError("limite_diario")

    custo = PRECOS_REFEICAO.get(aluno.categoria.value, Decimal("6.00"))
    if custo > 0 and Decimal(str(aluno.creditos)) < custo:
        raise RefeicaoError("saldo_insuficiente")

    if custo > 0:
        aluno.creditos = Decimal(str(aluno.creditos)) - custo

    refeicao = HistoricoRefeicao(
        aluno_id=aluno.id,
        tipo=tipo_enum,
        creditos_utilizados=custo,
        data_hora=datetime.utcnow(),
        registrado_por=admin_id,
    )
    db.add(refeicao)
    db.commit()
    db.refresh(refeicao)
    return refeicao
