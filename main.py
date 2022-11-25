"""Search your Libby libraries for books on your Goodreads want-to-read shelf."""
import atexit
import csv
import multiprocessing
import threading
import urllib.parse
from enum import Enum
from contextlib import contextmanager
from multiprocessing.pool import ThreadPool
from time import sleep
from typing import Dict, List

import click
import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel
from rich.progress import Progress
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

MOVE_ON_WHEN_BOOK_FOUND = False
NUM_THREADS = 32


class Driver:
    def __init__(self):
        # Normal Browser
        # self.driver = webdriver.Chrome(
        #     service=Service(ChromeDriverManager().install()),
        # )

        # Headless
        options = webdriver.ChromeOptions()
        options.headless = True
        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )


    def __del__(self):
        self.driver.quit() # clean up driver when we are cleaned up


threadLocal = threading.local()


def create_driver():
    the_driver = getattr(threadLocal, 'the_driver', None)
    if the_driver is None:
        the_driver = Driver()
        setattr(threadLocal, 'the_driver', the_driver)
    return the_driver.driver


class AvailabilityType(Enum):
    AVAILABLE = "AVAILABLE"
    OWNED = "OWNED"
    DEVOID = "DEVOID"
    ERROR = "ERROR"


class SearchRow(BaseModel):
    lib_name: str
    search_url: str
    title: str


class SearchResult(BaseModel):
    title: str
    lib_name: str
    avail: str
    audiobook: str
    ebook: str
    search_url: str

    def to_csv_row(self):
        """Return an array of fields, to be used by a csv writer."""
        return dict(self).values()


def parse_want_to_read_from_goodreads_export() -> List[Dict]:
    """Read in csv file formatted like a goodreads export.
    
    Returns all rows with books on the "to-read" shelf
    """
    all_books = []
    want_to_read = []
    with open("goodreads_library_export.csv", "r") as goodreads_export_csv:
        reader = csv.DictReader(goodreads_export_csv)
        all_books = list(reader)
        for row in all_books:
            if row["Exclusive Shelf"] == "to-read":
                want_to_read.append(row)
    return want_to_read


def find_book_at_lib(search_row):
    """Takes a search_row and executes the search."""
    driver = create_driver()

    driver.get(search_row.search_url)
    sleep(4)
    page: BeautifulSoup = BeautifulSoup(driver.page_source, features="html.parser")
    avail = AvailabilityType.ERROR.value
    if "No results." in page.text:
        avail = AvailabilityType.DEVOID.value
    elif "Borrow" in page.text:
        avail = AvailabilityType.AVAILABLE.value
    elif "Place Hold" in page.text:
        avail = AvailabilityType.OWNED.value

    audiobook = False
    ebook = False
    if "Play Sample" in page.text:
        audiobook = True
    if "Read Sample" in page.text:
        ebook = True
    
    return SearchResult(
        title=search_row.title,
        lib_name=search_row.lib_name,
        avail=avail,
        audiobook=audiobook,
        ebook=ebook,
        search_url=search_row.search_url
    )


def main():
    """Search your Libby libraries for books on your Goodreads want-to-read shelf."""
    
    # These lib URLs can be discovered by going to https://libbyapp.com/interview/menu#mainMenu
    # and clicking on your library. Your browser will make a request like:
    # https://ntc.api.overdrive.com/v1/provider-subscriptions?libraryKey=utahsonlinelibrary-provo&x-client-id=dewey
    # which will then redirect to a page like:
    # https://libbyapp.com/library/beehive
    libs = {
        "hawaii": "https://libbyapp.com/library/hawaii",
        "utah": "https://libbyapp.com/library/beehive",
        "idaho falls": "https://libbyapp.com/library/ifpl",
        "livermore": "https://libbyapp.com/library/livermore",
    }

    want_to_read = parse_want_to_read_from_goodreads_export()

    want_to_read_titles = [book["Title"] for book in want_to_read]
    total_books = len(want_to_read_titles)

    print(f"Number of to-read titles: {total_books}")
    print(f"Number of libraries: {len(libs.keys())}")
    print(f"Using {NUM_THREADS} threads")
    search_rows = []
    for title in want_to_read_titles:
        for lib_name, lib_url in libs.items():
            url_safe_title = urllib.parse.quote(title)
            search_url = f"{lib_url}/search/query-{url_safe_title}/page-1"
            search_rows.append(SearchRow(lib_name=lib_name, search_url=search_url, title=title))

    pool = ThreadPool(processes=NUM_THREADS)

    with Progress() as progress:
        task_progress = progress.add_task(f"[green]Searching {len(libs.keys())} libraries for {total_books} books...", total=len(search_rows))
        results = []
        requests_finished = 0
        for result in pool.imap(find_book_at_lib, search_rows):
            print(result)
            results.append(result)
            requests_finished += 1
            progress.update(task_progress, completed=requests_finished)

    pool.close()
    pool.join()

    with open("results.csv", "w") as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(["Title", "Library Name", "Availability", "Audiobook", "Ebook", "Search URL"])
        for result in results:
            csvwriter.writerow(result.to_csv_row())


if __name__ == "__main__":
    main()
