"""Синхронизация с YClients. Пока заглушка: сетевых вызовов нет.

Правила проекта «Кудри»:
- чтение каталога (филиалы, мастера, услуги) разрешено только как read-only;
- создание/перенос/отмена реальной записи в YClients только после явного
  подтверждения человека (confirmed=True) и проверки цепочки
  услуга -> направление -> мастер -> слот;
- токены берутся из защищённого хранилища (переменная окружения), в код не пишутся.
"""
import os


class YClientsSync:
    def __init__(self):
        self.token = os.environ.get("YCLIENTS_TOKEN")  # значение не читаем и не печатаем

    @property
    def configured(self):
        return bool(self.token)

    def pull_catalog(self):
        """Прочитать каталог из YClients (read-only). Не реализовано до подключения."""
        raise NotImplementedError("Подключение к YClients не настроено: нужен read-only доступ")

    def dry_run_push(self, con):
        """Показать, что БЫЛО БЫ отправлено в YClients. Ничего не отправляет."""
        rows = con.execute(
            "SELECT v.id, v.date, v.start, v.duration, m.name AS master, b.name AS branch "
            "FROM visits v JOIN masters m ON m.id=v.master_id JOIN branches b ON b.id=v.branch_id "
            "WHERE v.sync_state='pending' AND v.status!='cancelled' ORDER BY v.date, v.start").fetchall()
        return [dict(r) for r in rows]

    def push_visit(self, visit_id, confirmed=False):
        if not confirmed:
            raise PermissionError("Создание записи в YClients требует явного подтверждения человека")
        raise NotImplementedError("Боевая выгрузка не включена")
