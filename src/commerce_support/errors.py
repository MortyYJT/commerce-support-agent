class AppError(Exception):
    def __init__(self, code: str, public_message: str, status_code: int = 500) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.status_code = status_code


class BudgetExceeded(AppError):
    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(code, public_message, status_code=413)
