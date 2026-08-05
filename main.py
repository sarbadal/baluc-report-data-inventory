from app import create_app

app = create_app()


def entry_point():
    """Entry point for running the Flask app."""
    return app


if __name__ == "__main__":
	app.run(debug=True)

