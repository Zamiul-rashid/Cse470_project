"""Demo dataset for a fresh clone (Phase 5).

An empty NoteVault demos badly: the leaderboard is a list of zeroes, the
trending dashboard is blank, search returns nothing and the duplicate warning
has nothing to warn about. This script writes a dataset dense enough that every
read-only screen in Module 4 has something honest to show, and every flow in
the README walkthrough has a starting position.

Four decisions worth knowing before editing:

* **Real PDFs, real hashes.** Every listing gets a genuine one-page document
  built with ReportLab and its true SHA-256, written through the same
  ``services.storage`` preview path the upload endpoint uses. Faking the file
  would break preview, download and page counts -- the three things a grader
  clicks first. The two near-duplicate CSE220 listings deliberately share one
  identical byte string, so the exact-hash branch of ``check_duplicate`` fires
  as well as the fuzzy-title one.
* **States, not just rows.** The listings, transactions and rentals here sit at
  every point of their state machines -- including three rentals positioned so
  the T-3, T-1 and OVERDUE branches of the reminder job each have exactly one
  row to find on the next run.
* **Deterministic.** One seeded ``random.Random`` drives the demand-event
  spread, so two people seeding two machines see the same dashboard and can
  talk about the same numbers.
* **Never silently destructive.** Without ``--reset`` the script refuses to run
  against a database that already has users, because the alternative is
  doubling somebody's demo data ten minutes before they present.

Reviews are capped by the schema, not by taste: ``UNIQUE (transaction_id,
reviewer_id)`` allows two per completed transaction, so the six completed
transactions below carry twelve reviews -- both sides of every deal.

``materials_fts`` is never touched. The AFTER INSERT/UPDATE/DELETE triggers in
migration 0001 keep the index in step with ``study_materials`` on their own,
and writing to it by hand is how an external-content FTS5 index gets corrupted.

    cd backend && python scripts/seed.py --reset
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

# scripts/ is not a package and this is run as a plain file, so backend/ has to
# be importable before any `app.*` import resolves.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from pypdf import PdfReader  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.utils import simpleSplit  # noqa: E402
from reportlab.pdfgen.canvas import Canvas  # noqa: E402
from sqlalchemy import delete, func  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from sqlmodel import select  # noqa: E402

# recompute_rating lives with the endpoint that owns the two rating columns.
# Importing it rather than re-deriving the average here is what guarantees the
# seeded profile numbers are identical to what POST /reviews would have written.
from app.api.v1.reviews import recompute_rating  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.core.util import new_id, normalize_course_code, utcnow  # noqa: E402
from app.models import (  # noqa: E402
    Campus,
    Conversation,
    CourseDemandEvent,
    DemandEventType,
    ListingStatus,
    ListingType,
    Message,
    Notification,
    NotificationType,
    QRHandoff,
    RefreshToken,
    ReminderStage,
    Rental,
    Report,
    ReportReason,
    ReportStatus,
    Request,
    RequestStatus,
    Review,
    StudyMaterial,
    Transaction,
    TransactionStatus,
    User,
    UserRole,
    Wishlist,
    WishlistItem,
)
from app.services import storage  # noqa: E402
from app.services.analytics import analytics  # noqa: E402

#: Fixed so the trending numbers a teammate sees match the ones you quoted.
RANDOM_SEED = 20260812

ADMIN_PASSWORD = "admin1234"
STUDENT_PASSWORD = "student1234"

DEPT_CSE = "Computer Science and Engineering"
DEPT_MAT = "Mathematics"
DEPT_PHY = "Physics"
DEPT_ENG = "English"


# ---------------------------------------------------------------------------
# the fixture tables
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Course:
    subject: str
    department: str
    semester: str


#: Course code -> what it is and where it sits. ``course_code_norm`` is derived
#: from the key with normalize_course_code(), never hand-written.
COURSES: dict[str, Course] = {
    "CSE110": Course("Programming Language I", DEPT_CSE, "Spring 2025"),
    "CSE111": Course("Programming Language II", DEPT_CSE, "Summer 2025"),
    "CSE220": Course("Data Structures", DEPT_CSE, "Fall 2025"),
    "CSE221": Course("Algorithms", DEPT_CSE, "Spring 2026"),
    "CSE230": Course("Discrete Mathematics", DEPT_CSE, "Fall 2025"),
    "CSE250": Course("Circuits and Electronics", DEPT_CSE, "Summer 2025"),
    "CSE320": Course("Data Communications", DEPT_CSE, "Spring 2026"),
    "CSE340": Course("Computer Architecture", DEPT_CSE, "Fall 2025"),
    "CSE370": Course("Database Systems", DEPT_CSE, "Spring 2026"),
    "CSE420": Course("Compiler Design", DEPT_CSE, "Summer 2026"),
    "CSE470": Course("Software Engineering", DEPT_CSE, "Spring 2026"),
    "MAT110": Course("Differential Calculus", DEPT_MAT, "Spring 2025"),
    "MAT120": Course("Integral Calculus", DEPT_MAT, "Fall 2025"),
    "PHY111": Course("Principles of Physics I", DEPT_PHY, "Spring 2025"),
    "ENG101": Course("English Fundamentals", DEPT_ENG, "Summer 2025"),
}

CAMPUS_SPECS: tuple[tuple[str, str, str], ...] = (
    ("main", "Main Campus", "Dhaka"),
    ("merul", "Merul Badda Annex", "Dhaka"),
    ("savar", "Savar Extension", "Savar"),
)

#: (key, display name, email, campus key). Spread across all three campuses so
#: the campus filter (FR 4.3) changes what you see on every screen.
STUDENT_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("student1", "Ayesha Rahman", "student1@notevault.test", "main"),
    ("student2", "Tanvir Hasan", "student2@notevault.test", "main"),
    ("student3", "Nusrat Jahan", "student3@notevault.test", "main"),
    ("student4", "Rafiul Islam", "student4@notevault.test", "main"),
    ("student5", "Mahmuda Akter", "student5@notevault.test", "main"),
    ("student6", "Sabbir Ahmed", "student6@notevault.test", "merul"),
    ("student7", "Farhana Karim", "student7@notevault.test", "merul"),
    ("student8", "Imran Chowdhury", "student8@notevault.test", "merul"),
    ("student9", "Sadia Noor", "student9@notevault.test", "merul"),
    ("student10", "Zubair Alam", "student10@notevault.test", "savar"),
    ("student11", "Rumana Haque", "student11@notevault.test", "savar"),
)

ADMIN_SPEC: tuple[str, str, str, str] = (
    "admin",
    "Nadia Kabir",
    "admin@notevault.test",
    "main",
)


@dataclass(frozen=True, slots=True)
class MaterialSpec:
    key: str
    title: str
    course: str
    listing_type: ListingType
    price: float | None
    status: ListingStatus
    uploader: str
    days_ago: float
    edition: str | None = None
    description: str = ""
    note: str | None = None
    #: True on the second of the two near-duplicates: it reuses the first one's
    #: bytes verbatim, which is what gives the pair one shared SHA-256.
    shares_file_with: str | None = None


MATERIAL_SPECS: tuple[MaterialSpec, ...] = (
    # -- the catalogue a browsing student sees --------------------------------
    MaterialSpec(
        "m_cse110_notes",
        "Programming Language I - Complete Lecture Notes",
        "CSE110", ListingType.FREE, None, ListingStatus.APPROVED, "student1", 40,
        description="Week-by-week C notes for the whole semester, typed and cleaned up.",
    ),
    MaterialSpec(
        "m_cse110_lab",
        "CSE110 Lab Manual with Solved Exercises",
        "CSE110", ListingType.SELL, 250.0, ListingStatus.APPROVED, "student4", 33,
        edition="2nd",
        description="Every lab task with my working, including the two graded ones.",
    ),
    MaterialSpec(
        "m_cse111_oop",
        "Object-Oriented Programming in Java - CSE111 Notes",
        "CSE111", ListingType.SELL, 320.0, ListingStatus.APPROVED, "student2", 28,
        edition="3rd",
        description="Inheritance, interfaces and exception handling with runnable examples.",
    ),
    MaterialSpec(
        "m_cse111_slides",
        "CSE111 Slide Deck Bundle (Weeks 1-12)",
        "CSE111", ListingType.FREE, None, ListingStatus.APPROVED, "student6", 26,
        description="All lecture slides in one file, annotated during class.",
    ),
    # The deliberate near-duplicate pair: same course, same edition, titles that
    # score well above duplicate_title_threshold, and one identical file.
    MaterialSpec(
        "m_cse220_dup_a",
        "Data Structures Notes - CSE220 Complete",
        "CSE220", ListingType.SELL, 300.0, ListingStatus.APPROVED, "student2", 15,
        edition="2nd",
        description="Linked lists through graphs, with the diagrams redrawn legibly.",
    ),
    MaterialSpec(
        "m_cse220_dup_b",
        "CSE220 Data Structures Notes (Complete Set)",
        "CSE220", ListingType.RENT, 95.0, ListingStatus.APPROVED, "student7", 6,
        edition="2nd",
        description="Same coverage as the printed set going around the Merul batch.",
        shares_file_with="m_cse220_dup_a",
    ),
    MaterialSpec(
        "m_cse220_cheat",
        "CSE220 Data Structures Cheat Sheet",
        "CSE220", ListingType.FREE, None, ListingStatus.APPROVED, "student7", 12,
        description="One page of complexities for every structure on the syllabus.",
    ),
    MaterialSpec(
        "m_cse220_trees",
        "Linked Lists and Trees Worked Examples",
        "CSE220", ListingType.SELL, 280.0, ListingStatus.APPROVED, "student3", 20,
        description="Thirty solved problems, mostly from past finals.",
    ),
    MaterialSpec(
        "m_cse221_algo",
        "Algorithms Handbook - CSE221 Midterm Prep",
        "CSE221", ListingType.SELL, 480.0, ListingStatus.APPROVED, "student3", 21,
        edition="4th",
        description="Greedy, divide and conquer, and the graph algorithms in order.",
    ),
    MaterialSpec(
        "m_cse221_dp",
        "Dynamic Programming Problem Set with Solutions",
        "CSE221", ListingType.RENT, 90.0, ListingStatus.APPROVED, "student5", 19,
        description="Forty DP problems building from knapsack up to edit distance.",
    ),
    MaterialSpec(
        "m_cse230_discrete",
        "Discrete Mathematics - Proof Techniques Notes",
        "CSE230", ListingType.EXCHANGE, None, ListingStatus.APPROVED, "student8", 30,
        edition="2nd",
        description="Induction, contradiction and counting, with worked proofs.",
    ),
    MaterialSpec(
        "m_cse250_circuits",
        "Circuits and Electronics Lab Report Templates",
        "CSE250", ListingType.FREE, None, ListingStatus.APPROVED, "student9", 24,
        description="The report format our section was marked against, with one sample.",
    ),
    MaterialSpec(
        "m_cse320_datacomm",
        "Data Communications - Signal Encoding Notes",
        "CSE320", ListingType.SELL, 400.0, ListingStatus.APPROVED, "student10", 17,
        edition="5th",
        description="Encoding schemes, framing and error detection with the diagrams.",
    ),
    MaterialSpec(
        "m_cse340_arch",
        "Computer Architecture - MIPS Assembly Workbook",
        "CSE340", ListingType.RENT, 120.0, ListingStatus.APPROVED, "student2", 16,
        edition="3rd",
        description="Datapath, pipelining and a full set of assembly exercises.",
    ),
    MaterialSpec(
        "m_cse370_sql",
        "Database Systems - SQL Query Practice Pack",
        "CSE370", ListingType.FREE, None, ListingStatus.APPROVED, "student4", 14,
        description="Sixty queries against a sample schema, answers at the back.",
    ),
    MaterialSpec(
        "m_cse370_er",
        "ER Modelling and Normalisation Worked Examples",
        "CSE370", ListingType.SELL, 300.0, ListingStatus.APPROVED, "student7", 11,
        edition="2nd",
        description="Every normal form up to BCNF with the decomposition steps shown.",
    ),
    MaterialSpec(
        "m_cse420_compiler",
        "Compiler Design - Parsing Techniques Summary",
        "CSE420", ListingType.EXCHANGE, None, ListingStatus.APPROVED, "student8", 22,
        description="LL and LR parsing tables built by hand, step by step.",
    ),
    MaterialSpec(
        "m_cse470_srs",
        "Software Engineering - SRS Writing Guide",
        "CSE470", ListingType.SELL, 350.0, ListingStatus.APPROVED, "student1", 9,
        edition="3rd",
        description="How our section's SRS was structured, plus the marking rubric.",
    ),
    MaterialSpec(
        "m_cse470_uml",
        "CSE470 UML Diagram Pack (Use Case, Class, Sequence)",
        "CSE470", ListingType.FREE, None, ListingStatus.APPROVED, "student5", 8,
        description="Editable diagrams for a full project, drawn to the notation taught.",
    ),
    MaterialSpec(
        "m_cse470_agile",
        "Agile and Scrum Revision Notes for CSE470",
        "CSE470", ListingType.RENT, 110.0, ListingStatus.APPROVED, "student6", 7,
        edition="2nd",
        description="Sprint mechanics, estimation and the exam questions that repeat.",
    ),
    MaterialSpec(
        "m_mat110_diff",
        "Differential Calculus - Solved Past Papers",
        "MAT110", ListingType.SELL, 260.0, ListingStatus.APPROVED, "student3", 27,
        edition="6th",
        description="Six years of finals with full working for every question.",
    ),
    MaterialSpec(
        "m_mat120_int",
        "Integral Calculus - Formula Book and Worked Sums",
        "MAT120", ListingType.SELL, 280.0, ListingStatus.APPROVED, "student9", 13,
        edition="6th",
        description="Standard integrals, substitution drills and the series chapter.",
    ),
    MaterialSpec(
        "m_mat120_tut",
        "MAT120 Tutorial Sheets with Answer Keys",
        "MAT120", ListingType.FREE, None, ListingStatus.APPROVED, "student11", 10,
        description="All twelve tutorial sheets, answers checked against the TA's.",
    ),
    MaterialSpec(
        "m_phy111_mech",
        "Principles of Physics I - Mechanics Notes",
        "PHY111", ListingType.RENT, 130.0, ListingStatus.APPROVED, "student10", 20,
        edition="10th",
        description="Kinematics to rotational motion, with the derivations written out.",
    ),
    MaterialSpec(
        "m_eng101_writing",
        "English Fundamentals - Academic Writing Handbook",
        "ENG101", ListingType.SELL, 200.0, ListingStatus.APPROVED, "student11", 23,
        edition="2nd",
        description="Paragraph structure, citation and two annotated model essays.",
    ),
    MaterialSpec(
        "m_eng101_grammar",
        "ENG101 Grammar Drills and Model Answers",
        "ENG101", ListingType.EXCHANGE, None, ListingStatus.APPROVED, "student1", 25,
        description="Tense, article and preposition drills with the answer key.",
    ),
    # -- sold and rented out: the six completed transactions ------------------
    MaterialSpec(
        "m_cse221_book",
        "Introduction to Algorithms - Chapter Summaries",
        "CSE221", ListingType.SELL, 450.0, ListingStatus.COMPLETED, "student1", 45,
        edition="3rd",
        description="Chapter-by-chapter summaries of the recommended text.",
    ),
    MaterialSpec(
        "m_cse370_book",
        "Database Management Systems - Course Notes Compilation",
        "CSE370", ListingType.SELL, 520.0, ListingStatus.COMPLETED, "student2", 42,
        edition="3rd",
        description="Transactions, indexing and recovery, compiled from four sections.",
    ),
    MaterialSpec(
        "m_cse110_starter",
        "CSE110 Starter Problem Set (Weeks 1-6)",
        "CSE110", ListingType.FREE, None, ListingStatus.COMPLETED, "student3", 38,
        description="Loops, arrays and functions for anyone starting from zero.",
    ),
    MaterialSpec(
        "m_cse470_rent",
        "Software Engineering - Pressman Chapter Notes",
        "CSE470", ListingType.RENT, 140.0, ListingStatus.COMPLETED, "student4", 36,
        edition="8th",
        description="Process models, requirements and testing, in one bound copy.",
    ),
    MaterialSpec(
        "m_mat120_rent",
        "MAT120 Integral Calculus - Reference Copy",
        "MAT120", ListingType.RENT, 100.0, ListingStatus.COMPLETED, "student5", 35,
        edition="6th",
        description="The reference copy the batch passes around before finals.",
    ),
    MaterialSpec(
        "m_phy111_rent",
        "Physics I - Resnick Halliday Reference Copy",
        "PHY111", ListingType.RENT, 150.0, ListingStatus.COMPLETED, "student6", 50,
        edition="10th",
        description="Reference copy with the problem sets bookmarked.",
    ),
    # -- reserved: the six transactions still in flight -----------------------
    MaterialSpec(
        "m_cse220_reserved",
        "CSE220 Midterm Question Bank (2019-2024)",
        "CSE220", ListingType.SELL, 220.0, ListingStatus.RESERVED, "student8", 12,
        description="Every midterm paper for six years, with handwritten solutions.",
    ),
    MaterialSpec(
        "m_cse230_reserved",
        "Discrete Math - Graph Theory Problem Set",
        "CSE230", ListingType.RENT, 80.0, ListingStatus.RESERVED, "student9", 11,
        description="Trees, colouring and matching problems with hints.",
    ),
    MaterialSpec(
        "m_cse340_reserved",
        "Computer Architecture - Cache Design Notes",
        "CSE340", ListingType.SELL, 270.0, ListingStatus.RESERVED, "student10", 10,
        edition="2nd",
        description="Cache mapping and the memory hierarchy worked through.",
    ),
    MaterialSpec(
        "m_cse470_reserved",
        "CSE470 Project Report Template Pack",
        "CSE470", ListingType.FREE, None, ListingStatus.RESERVED, "student11", 9,
        description="Report skeleton, traceability matrix and a test plan template.",
    ),
    MaterialSpec(
        "m_mat110_reserved",
        "MAT110 Final Exam Survival Notes",
        "MAT110", ListingType.EXCHANGE, None, ListingStatus.RESERVED, "student1", 8,
        description="The twelve question types that actually come up, condensed.",
    ),
    MaterialSpec(
        "m_cse111_reserved",
        "CSE111 Recursion and Pointers Workbook",
        "CSE111", ListingType.RENT, 100.0, ListingStatus.RESERVED, "student2", 7,
        edition="2nd",
        description="Recursion traces drawn out, then the same problems iteratively.",
    ),
    # -- the moderation queue (FR 4.1) ---------------------------------------
    MaterialSpec(
        "m_pending_cse320",
        "CSE320 Networking Lab Cheat Sheet",
        "CSE320", ListingType.FREE, None, ListingStatus.PENDING, "student3", 2,
        description="Subnetting and the Cisco commands needed in the lab exam.",
    ),
    MaterialSpec(
        "m_pending_cse420",
        "Compiler Design - Lex and Yacc Examples",
        "CSE420", ListingType.SELL, 340.0, ListingStatus.PENDING, "student7", 1,
        edition="2nd",
        description="A working toy compiler, commented line by line.",
    ),
    MaterialSpec(
        "m_pending_mat120",
        "MAT120 Integration Techniques - Handwritten",
        "MAT120", ListingType.RENT, 70.0, ListingStatus.PENDING, "student11", 0,
        description="Scanned handwritten notes, legible and complete.",
    ),
    MaterialSpec(
        "m_rejected",
        "Scanned Textbook: Complete Solutions Manual",
        "CSE221", ListingType.SELL, 900.0, ListingStatus.REJECTED, "student8", 5,
        edition="3rd",
        description="Full solutions manual, scanned cover to cover.",
        note=(
            "Rejected: this is a publisher's solutions manual, which NoteVault "
            "cannot host. Post your own notes or worked solutions instead."
        ),
    ),
    MaterialSpec(
        "m_removed",
        "CSE250 Formula Sheet (scanned)",
        "CSE250", ListingType.SELL, 150.0, ListingStatus.REMOVED, "student10", 18,
        description="Formula sheet lifted from the lab manual.",
        note="Removed after a copyright report was upheld.",
    ),
)


@dataclass(frozen=True, slots=True)
class RentalSpec:
    """A rental term expressed the way the reminder job reads it.

    ``due_in_days`` is relative to now and negative means overdue, so the three
    seeded rentals land in the T-3, T-1 and OVERDUE buckets by construction
    rather than by whatever date the machine happens to be set to.
    """

    term_days: int
    due_in_days: float
    stage: ReminderStage


@dataclass(frozen=True, slots=True)
class TransactionSpec:
    key: str
    listing: str
    buyer: str
    status: TransactionStatus
    days_ago: float
    completed_days_ago: float | None = None
    agreed_price: float | None = None
    rental: RentalSpec | None = None


TRANSACTION_SPECS: tuple[TransactionSpec, ...] = (
    # Six COMPLETED, three of them rentals whose clocks are set so the daily
    # reminder job has one row in each of its three stages on the next run.
    TransactionSpec(
        "t1", "m_cse221_book", "student6", TransactionStatus.COMPLETED, 42,
        completed_days_ago=40, agreed_price=420.0,
    ),
    TransactionSpec(
        "t2", "m_cse370_book", "student7", TransactionStatus.COMPLETED, 39,
        completed_days_ago=37,
    ),
    TransactionSpec(
        "t3", "m_cse110_starter", "student8", TransactionStatus.COMPLETED, 36,
        completed_days_ago=34,
    ),
    TransactionSpec(
        "t4", "m_cse470_rent", "student9", TransactionStatus.COMPLETED, 14,
        rental=RentalSpec(14, 2.0, ReminderStage.NONE),
    ),
    TransactionSpec(
        "t5", "m_mat120_rent", "student10", TransactionStatus.COMPLETED, 22,
        rental=RentalSpec(21, 1.0, ReminderStage.T_MINUS_3),
    ),
    TransactionSpec(
        "t6", "m_phy111_rent", "student11", TransactionStatus.COMPLETED, 36,
        rental=RentalSpec(30, -4.0, ReminderStage.T_MINUS_1),
    ),
    # In flight. Their listings are RESERVED, which is what stops a second
    # buyer starting a competing transaction.
    TransactionSpec("t7", "m_cse220_reserved", "student10", TransactionStatus.REQUESTED, 3),
    TransactionSpec(
        "t8", "m_cse230_reserved", "student2", TransactionStatus.REQUESTED, 2,
        # services.transactions writes the rental row at REQUESTED, because
        # `transactions` has nowhere else to store the agreed term.
        rental=RentalSpec(14, 12.0, ReminderStage.NONE),
    ),
    TransactionSpec("t9", "m_cse340_reserved", "student3", TransactionStatus.ACCEPTED, 5),
    TransactionSpec("t10", "m_cse470_reserved", "student7", TransactionStatus.ACCEPTED, 4),
    TransactionSpec("t11", "m_mat110_reserved", "student9", TransactionStatus.ACCEPTED, 6),
    TransactionSpec(
        "t12", "m_cse111_reserved", "student10", TransactionStatus.AWAITING_HANDOFF, 3,
        # Rebased at ACCEPTED two days ago, exactly as the state machine does.
        rental=RentalSpec(14, 12.0, ReminderStage.NONE),
    ),
    # Cancelled: the listings went back to APPROVED and their provisional
    # rentals were deleted, so there is nothing left but the history row.
    TransactionSpec("t13", "m_cse221_algo", "student11", TransactionStatus.CANCELLED, 12),
    TransactionSpec("t14", "m_cse370_er", "student4", TransactionStatus.CANCELLED, 9),
    TransactionSpec("t15", "m_cse470_agile", "student3", TransactionStatus.CANCELLED, 6),
)


@dataclass(frozen=True, slots=True)
class ReviewSpec:
    transaction: str
    #: Which side wrote it. The reviewee is derived, never stated -- same rule
    #: the POST /reviews handler enforces.
    author: str
    rating: int
    comment: str
    hours_after: int = 6


REVIEW_SPECS: tuple[ReviewSpec, ...] = (
    ReviewSpec(
        "t1", "buyer", 5, "Exactly the chapter summaries I needed. Handoff took two minutes."
    ),
    ReviewSpec("t1", "seller", 5, "Turned up on time and paid on the spot.", 9),
    ReviewSpec(
        "t2", "buyer", 4, "Good compilation, though a few pages are photocopied faintly."
    ),
    ReviewSpec("t2", "seller", 5, "Easy to arrange, met me outside the department.", 11),
    ReviewSpec(
        "t3", "buyer", 5, "Free and genuinely useful - saved me a week of catching up."
    ),
    ReviewSpec("t3", "seller", 4, "Polite and quick to reply.", 20),
    ReviewSpec(
        "t4", "buyer", 4, "Clean copy, all the diagrams intact. Rental terms were clear."
    ),
    ReviewSpec(
        "t4", "seller", 5, "Careful with the book and agreed the return date up front.", 8
    ),
    ReviewSpec(
        "t5", "buyer", 3, "Useful, but several pages were too faint to read comfortably."
    ),
    ReviewSpec("t5", "seller", 4, "Fair feedback, no trouble at all.", 30),
    ReviewSpec("t6", "buyer", 5, "The reference copy everyone fights over. Worth it."),
    # Written 31 days after the handoff, which is what makes it land *after*
    # that rental's due date -- the complaint has to be chronologically possible.
    ReviewSpec(
        "t6", "seller", 2,
        "Still has not returned the book and it is past the due date.",
        hours_after=744,
    ),
)


@dataclass(frozen=True, slots=True)
class RequestSpec:
    key: str
    requester: str
    course: str
    status: RequestStatus
    days_ago: float
    edition: str | None = None
    listing_type: ListingType | None = None
    max_price: float | None = None
    campus: str | None = None
    description: str = ""


REQUEST_SPECS: tuple[RequestSpec, ...] = (
    RequestSpec(
        "r1", "student2", "CSE470", RequestStatus.OPEN, 5,
        edition="8th", listing_type=ListingType.SELL, max_price=400.0, campus="main",
        description="Looking for Pressman notes before the midterm. Can collect on campus.",
    ),
    RequestSpec(
        "r2", "student3", "CSE220", RequestStatus.OPEN, 4,
        listing_type=ListingType.RENT, max_price=150.0,
        description="Happy to rent for three weeks, any campus.",
    ),
    RequestSpec(
        "r3", "student7", "MAT120", RequestStatus.OPEN, 8,
        edition="6th", campus="merul",
        description="Need the integral calculus formula book, buying or renting.",
    ),
    RequestSpec(
        "r4", "student9", "CSE370", RequestStatus.OPEN, 6,
        listing_type=ListingType.SELL, max_price=350.0,
        description="Anything covering normalisation with worked decompositions.",
    ),
    RequestSpec(
        "r5", "student10", "CSE221", RequestStatus.OPEN, 9,
        edition="3rd", campus="savar",
        description="Algorithms notes for the Savar section, ideally with past papers.",
    ),
    RequestSpec(
        "r6", "student11", "CSE470", RequestStatus.OPEN, 3,
        listing_type=ListingType.FREE,
        description="Any free CSE470 material - diagrams especially.",
    ),
    RequestSpec(
        "r7", "student5", "CSE320", RequestStatus.OPEN, 7,
        max_price=300.0, campus="main",
        description="Data communications notes, encoding chapter above all.",
    ),
    RequestSpec(
        "r8", "student8", "MAT120", RequestStatus.OPEN, 2,
        listing_type=ListingType.RENT, max_price=120.0, campus="merul",
        description="Renting for the last two weeks of the semester is fine.",
    ),
    # Already answered by an approved listing, which is what the matcher does.
    RequestSpec(
        "r9", "student6", "CSE110", RequestStatus.MATCHED, 41,
        listing_type=ListingType.FREE, campus="main",
        description="Starting CSE110 late, need notes from the beginning.",
    ),
    RequestSpec(
        "r10", "student4", "CSE220", RequestStatus.MATCHED, 16,
        edition="2nd", listing_type=ListingType.SELL, max_price=350.0, campus="main",
        description="Data structures notes, second edition, buying outright.",
    ),
)

#: Bookmarks (FR 2.4). Never a student's own listing -- you cannot want what
#: you are already selling, and the wishlist matcher skips uploaders anyway.
WISHLIST_ITEMS: dict[str, tuple[str, ...]] = {
    "student1": ("m_cse370_sql", "m_cse221_algo", "m_mat120_int"),
    "student2": ("m_cse470_srs", "m_cse470_uml", "m_phy111_mech", "m_cse220_cheat"),
    "student3": ("m_cse220_dup_a", "m_cse470_agile"),
    "student4": ("m_cse220_dup_b", "m_mat120_tut", "m_cse320_datacomm"),
    "student5": ("m_cse470_srs", "m_eng101_writing"),
    "student6": ("m_cse220_cheat", "m_mat120_int", "m_cse370_er"),
    "student7": ("m_cse470_uml", "m_mat110_diff", "m_cse230_discrete"),
    "student8": ("m_cse470_agile", "m_mat120_tut"),
    "student9": ("m_cse220_dup_a", "m_cse110_lab", "m_cse470_srs"),
    "student10": ("m_cse470_uml", "m_mat120_int"),
    "student11": ("m_cse221_dp", "m_cse370_sql", "m_cse220_cheat"),
}


@dataclass(frozen=True, slots=True)
class ConversationSpec:
    key: str
    user_one: str
    user_two: str
    listing: str
    started_days_ago: float
    #: (sender key, body). Timestamps are spaced evenly from the start.
    messages: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    #: How many trailing messages stay unread, so the inbox badge is non-zero.
    unread_tail: int = 1


CONVERSATION_SPECS: tuple[ConversationSpec, ...] = (
    ConversationSpec(
        "c1", "student2", "student1", "m_cse470_srs", 1.5,
        messages=(
            ("student2", "Assalamu alaikum - is the SRS writing guide still available?"),
            ("student1", "Walaikum assalam. Yes, it is. I am on Main Campus most days."),
            ("student2", "Would you take 300? I can collect after the 11am class tomorrow."),
            ("student1", "320 and I will include the rubric our section was graded on."),
            ("student2", "Deal. Library entrance works for me."),
            ("student1", "Perfect, I will bring it. Message me when you are there."),
        ),
    ),
    ConversationSpec(
        "c2", "student9", "student4", "m_cse470_rent", 12,
        messages=(
            ("student9", "Got the Pressman notes, thank you. The QR scan worked first time."),
            ("student4", "Glad it went smoothly. Two weeks on the return, no rush."),
            ("student9", "Noted. Would the 14th be alright instead?"),
            ("student4", "That is fine, just message me the day before."),
            ("student9", "Will do. I have left you a review as well."),
        ),
    ),
    ConversationSpec(
        "c3", "student10", "student8", "m_cse220_reserved", 3.2,
        messages=(
            ("student10", "Is the CSE220 question bank still up for sale?"),
            ("student8", "Yes - it covers 2019 to 2024, including the two retake papers."),
            ("student10", "Do the solutions come with it?"),
            ("student8", "Handwritten solutions for the last three years only."),
            ("student10", "That works. I have sent a request through the listing."),
            ("student8", "Saw it. I will accept once I am back at Merul Badda tomorrow."),
            ("student10", "No problem, I am not in a hurry."),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class ReportSpec:
    reporter: str
    listing: str
    reason: ReportReason
    status: ReportStatus
    days_ago: float
    details: str
    resolution_note: str | None = None
    resolved_days_ago: float | None = None


REPORT_SPECS: tuple[ReportSpec, ...] = (
    ReportSpec(
        "student5", "m_cse111_oop", ReportReason.COPYRIGHT, ReportStatus.OPEN, 4,
        details="This looks like a scan of the official textbook rather than personal notes.",
    ),
    ReportSpec(
        "student9", "m_eng101_grammar", ReportReason.SPAM, ReportStatus.OPEN, 2,
        details="The same file has been posted three times under different titles.",
    ),
    ReportSpec(
        "student3", "m_removed", ReportReason.COPYRIGHT, ReportStatus.RESOLVED, 17,
        details="Pages 1-6 are photocopied straight out of the lab manual.",
        resolution_note=(
            "Upheld. The listing reproduced the lab manual verbatim; it has been "
            "removed and the uploader warned."
        ),
        resolved_days_ago=16,
    ),
)


@dataclass(frozen=True, slots=True)
class NotificationSpec:
    user: str
    type: NotificationType
    message: str
    ref_type: str
    ref_key: str
    is_read: bool
    days_ago: float


NOTIFICATION_SPECS: tuple[NotificationSpec, ...] = (
    NotificationSpec(
        "student4", NotificationType.REQUEST_MATCH,
        "A listing matching your request for CSE220 is now available: "
        "Data Structures Notes - CSE220 Complete",
        "listing", "m_cse220_dup_a", True, 14,
    ),
    NotificationSpec(
        "student6", NotificationType.REQUEST_MATCH,
        "A listing matching your request for CSE110 is now available: "
        "Programming Language I - Complete Lecture Notes",
        "listing", "m_cse110_notes", True, 39,
    ),
    NotificationSpec(
        "student2", NotificationType.WISHLIST_MATCH,
        "New CSE470 material you might want: CSE470 UML Diagram Pack "
        "(Use Case, Class, Sequence)",
        "listing", "m_cse470_uml", True, 7,
    ),
    # The two reminders that have already fired, matching last_reminder_stage
    # on their rentals. The T-3 for t4 is deliberately absent: that rental is
    # still at stage NONE so the job has real work on its next run.
    NotificationSpec(
        "student10", NotificationType.RENTAL_DUE,
        "'MAT120 Integral Calculus - Reference Copy' is due back in 3 days.",
        "transaction", "t5", False, 2,
    ),
    NotificationSpec(
        "student11", NotificationType.RENTAL_DUE,
        "'Physics I - Resnick Halliday Reference Copy' is due back in 3 days.",
        "transaction", "t6", True, 7,
    ),
    NotificationSpec(
        "student11", NotificationType.RENTAL_DUE,
        "'Physics I - Resnick Halliday Reference Copy' is due back tomorrow.",
        "transaction", "t6", False, 5,
    ),
    NotificationSpec(
        "student1", NotificationType.NEW_REVIEW,
        "Sabbir Ahmed left you a 5-star review for "
        "'Introduction to Algorithms - Chapter Summaries'.",
        "transaction", "t1", True, 39,
    ),
    NotificationSpec(
        "student6", NotificationType.NEW_REVIEW,
        "Ayesha Rahman left you a 5-star review for "
        "'Introduction to Algorithms - Chapter Summaries'.",
        "transaction", "t1", False, 39,
    ),
    NotificationSpec(
        "student8", NotificationType.TRANSACTION_UPDATE,
        "Zubair Alam wants your listing 'CSE220 Midterm Question Bank (2019-2024)'.",
        "transaction", "t7", False, 3,
    ),
    NotificationSpec(
        "student3", NotificationType.TRANSACTION_UPDATE,
        "Your request for 'Computer Architecture - Cache Design Notes' was accepted. "
        "Arrange the handoff.",
        "transaction", "t9", True, 4,
    ),
    NotificationSpec(
        "student10", NotificationType.TRANSACTION_UPDATE,
        "'CSE111 Recursion and Pointers Workbook' is ready for handoff - "
        "scan the QR code to confirm.",
        "transaction", "t12", False, 1,
    ),
    NotificationSpec(
        "student8", NotificationType.MODERATION_RESULT,
        "Your listing 'Scanned Textbook: Complete Solutions Manual' was rejected. "
        "See the moderator's note.",
        "listing", "m_rejected", False, 4,
    ),
    NotificationSpec(
        "student1", NotificationType.NEW_MESSAGE,
        "Tanvir Hasan sent you a message about "
        "'Software Engineering - SRS Writing Guide'.",
        "conversation", "c1", False, 1,
    ),
)

#: (course, event count, hot). The three hot courses draw a demand mix skewed
#: towards REQUEST and WISHLIST_ADD -- the two heavy weights in DEMAND_WEIGHTS
#: -- which is what puts CSE470, CSE220 and MAT120 on top of the dashboard
#: rather than a raw volume difference nobody can explain.
DEMAND_PLAN: tuple[tuple[str, int, bool], ...] = (
    ("CSE470", 60, True),
    ("CSE220", 55, True),
    ("MAT120", 48, True),
    ("CSE221", 34, False),
    ("CSE370", 30, False),
    ("CSE110", 26, False),
    ("CSE111", 22, False),
    ("MAT110", 20, False),
    ("CSE230", 18, False),
    ("PHY111", 18, False),
    ("CSE320", 16, False),
    ("CSE340", 15, False),
    ("CSE420", 14, False),
    ("CSE250", 12, False),
    ("ENG101", 12, False),
)

_HOT_MIX: tuple[tuple[DemandEventType, float], ...] = (
    (DemandEventType.REQUEST, 0.22),
    (DemandEventType.WISHLIST_ADD, 0.28),
    (DemandEventType.SEARCH, 0.35),
    (DemandEventType.VIEW, 0.15),
)
_BASE_MIX: tuple[tuple[DemandEventType, float], ...] = (
    (DemandEventType.REQUEST, 0.06),
    (DemandEventType.WISHLIST_ADD, 0.12),
    (DemandEventType.SEARCH, 0.42),
    (DemandEventType.VIEW, 0.40),
)

#: Main Campus is the biggest, so it generates the most signal. Keeps the
#: campus-scoped trending view meaningfully different from the global one.
_CAMPUS_MIX: tuple[float, ...] = (0.5, 0.3, 0.2)


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

_PAGE_WIDTH, _PAGE_HEIGHT = A4
_MARGIN = 56.0

_BODY_LINES: tuple[str, ...] = (
    "1.  Scope of these notes",
    "    Compiled during the semester and cleaned up afterwards. Section",
    "    numbering follows the course outline handed out in week one.",
    "",
    "2.  How to use this document",
    "    Worked examples are boxed; anything marked with an asterisk came up",
    "    in a past final. Definitions are stated before they are used.",
    "",
    "3.  Contents",
    "    3.1  Core definitions and notation",
    "    3.2  Worked examples, in increasing difficulty",
    "    3.3  Past-paper questions with full solutions",
    "    3.4  A one-page summary for the night before",
)


def _build_pdf(*, title: str, course_code: str, course: Course, uploader: str) -> bytes:
    """One real page: title block, course metadata, body, SAMPLE watermark.

    Built in memory and returned as bytes so the two near-duplicate listings can
    be handed the *same* bytes and therefore the same SHA-256. Writing the same
    document twice would not do it -- ReportLab stamps a creation timestamp into
    every file, so two renders of identical content hash differently.
    """
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    canvas.setTitle(title)
    canvas.setAuthor(uploader)
    canvas.setSubject(f"{course_code} - {course.subject}")

    # Watermark first, so the text sits on top of it rather than under it.
    canvas.saveState()
    canvas.setFillGray(0.87)
    canvas.setFont("Helvetica-Bold", 96)
    canvas.translate(_PAGE_WIDTH / 2, _PAGE_HEIGHT / 2)
    canvas.rotate(38)
    canvas.drawCentredString(0, 0, "SAMPLE")
    canvas.restoreState()

    cursor = _PAGE_HEIGHT - _MARGIN - 20
    canvas.setFillGray(0.35)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(_MARGIN, cursor, f"{course_code}  |  {course.subject}")

    cursor -= 34
    canvas.setFillGray(0.0)
    canvas.setFont("Helvetica-Bold", 19)
    for line in simpleSplit(title, "Helvetica-Bold", 19, _PAGE_WIDTH - 2 * _MARGIN):
        canvas.drawString(_MARGIN, cursor, line)
        cursor -= 24

    cursor -= 6
    canvas.setFillGray(0.4)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(
        _MARGIN, cursor, f"{course.department}  -  {course.semester}  -  {uploader}"
    )

    cursor -= 12
    canvas.setStrokeGray(0.75)
    canvas.line(_MARGIN, cursor, _PAGE_WIDTH - _MARGIN, cursor)

    cursor -= 30
    canvas.setFillGray(0.15)
    canvas.setFont("Helvetica", 11)
    for line in _BODY_LINES:
        canvas.drawString(_MARGIN, cursor, line)
        cursor -= 16

    canvas.setFillGray(0.5)
    canvas.setFont("Helvetica-Oblique", 8.5)
    canvas.drawString(
        _MARGIN,
        _MARGIN,
        "NoteVault demo data - generated by scripts/seed.py, not real course material.",
    )

    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


@dataclass(frozen=True, slots=True)
class StoredFile:
    file_path: str
    preview_path: str | None
    page_count: int | None
    file_hash: str


def _relative(path: Path) -> str:
    """Repo-relative POSIX path, the convention services.storage stores."""
    return path.resolve().relative_to(settings.repo_root.resolve()).as_posix()


def _store_pdf(data: bytes) -> StoredFile:
    """Write one document plus its preview, exactly as an upload would.

    The preview goes through ``storage.generate_preview`` rather than a copy so
    the seeded rows exercise the same pypdf slicing path FR 1.4 depends on.
    """
    target = settings.materials_dir / f"{new_id()}.pdf"
    target.write_bytes(data)
    return StoredFile(
        file_path=_relative(target),
        preview_path=storage.generate_preview(target, storage.PDF_MIME),
        page_count=len(PdfReader(BytesIO(data)).pages),
        file_hash=hashlib.sha256(data).hexdigest(),
    )


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

#: Children before parents; ``foreign_keys=ON`` means the order is load-bearing.
_DELETE_ORDER = (
    CourseDemandEvent,
    Notification,
    Review,
    Report,
    QRHandoff,
    Rental,
    Transaction,
    Message,
    Conversation,
    WishlistItem,
    Wishlist,
    Request,
    StudyMaterial,
    RefreshToken,
    User,
    Campus,
)


async def reset(session: AsyncSession) -> int:
    """Drop every row, and the files the dropped rows owned. Returns file count.

    Only files referenced by ``study_materials`` are removed -- emptying the
    storage directories wholesale would take out uploads that arrived after the
    last seed and belong to nobody's demo.

    Deleting ``study_materials`` fires the FTS delete trigger row by row, which
    is why ``materials_fts`` needs no attention here.
    """
    rows = (
        await session.execute(
            select(StudyMaterial.file_path, StudyMaterial.preview_path)
        )
    ).all()
    removed = 0
    for file_path, preview_path in rows:
        removed += sum(
            1 for path in (file_path, preview_path) if storage.delete_stored_file(path)
        )

    for model in _DELETE_ORDER:
        await session.execute(delete(model))
    await session.commit()
    return removed


# ---------------------------------------------------------------------------
# seeding, in dependency order
# ---------------------------------------------------------------------------


async def seed_campuses(session: AsyncSession) -> dict[str, Campus]:
    campuses = {
        key: Campus(campus_id=new_id(), name=name, location=location)
        for key, name, location in CAMPUS_SPECS
    }
    session.add_all(list(campuses.values()))
    await session.commit()
    return campuses


async def seed_users(
    session: AsyncSession, campuses: dict[str, Campus], now: datetime
) -> dict[str, User]:
    """Users plus their single Wishlist row, the pair registration writes."""
    admin_key, admin_name, admin_email, admin_campus = ADMIN_SPEC
    users: dict[str, User] = {
        admin_key: User(
            user_id=new_id(),
            name=admin_name,
            email=admin_email,
            password_hash=hash_password(ADMIN_PASSWORD),
            role=UserRole.ADMIN.value,
            campus_id=campuses[admin_campus].campus_id,
            created_at=now - timedelta(days=120),
        )
    }
    for index, (key, name, email, campus_key) in enumerate(STUDENT_SPECS):
        users[key] = User(
            user_id=new_id(),
            name=name,
            email=email,
            password_hash=hash_password(STUDENT_PASSWORD),
            role=UserRole.STUDENT.value,
            campus_id=campuses[campus_key].campus_id,
            # Staggered joins so "member since" is not identical for everyone.
            created_at=now - timedelta(days=110 - index * 6),
        )
    session.add_all(list(users.values()))
    await session.flush()

    session.add_all([Wishlist(user_id=user.user_id) for user in users.values()])
    await session.commit()
    return users


async def seed_materials(
    session: AsyncSession,
    users: dict[str, User],
    now: datetime,
    rng: random.Random,
) -> dict[str, StudyMaterial]:
    materials: dict[str, StudyMaterial] = {}
    documents: dict[str, bytes] = {}
    admin = users["admin"]

    for spec in MATERIAL_SPECS:
        uploader = users[spec.uploader]
        course = COURSES[spec.course]

        if spec.shares_file_with is not None:
            # Re-uploading a file somebody else already posted is precisely the
            # case the exact-hash branch of check_duplicate exists to catch.
            data = documents[spec.shares_file_with]
        else:
            data = _build_pdf(
                title=spec.title,
                course_code=spec.course,
                course=course,
                uploader=uploader.name,
            )
        documents[spec.key] = data
        stored = _store_pdf(data)

        uploaded_at = now - timedelta(days=spec.days_ago, hours=rng.uniform(0, 9))
        moderated = spec.status != ListingStatus.PENDING
        materials[spec.key] = StudyMaterial(
            listing_id=new_id(),
            uploader_id=uploader.user_id,
            campus_id=uploader.campus_id,
            title=spec.title,
            description=spec.description or None,
            course_code=spec.course,
            course_code_norm=normalize_course_code(spec.course),
            department=course.department,
            semester=course.semester,
            edition=spec.edition,
            listing_type=spec.listing_type.value,
            price=spec.price,
            status=spec.status.value,
            file_path=stored.file_path,
            preview_path=stored.preview_path,
            page_count=stored.page_count,
            file_hash=stored.file_hash,
            upload_date=uploaded_at,
            moderated_by=admin.user_id if moderated else None,
            moderated_at=(
                uploaded_at + timedelta(hours=rng.uniform(2, 20)) if moderated else None
            ),
            moderation_note=spec.note,
        )

    session.add_all(list(materials.values()))
    await session.commit()
    return materials


async def seed_transactions(
    session: AsyncSession,
    users: dict[str, User],
    materials: dict[str, StudyMaterial],
    now: datetime,
) -> dict[str, Transaction]:
    transactions: dict[str, Transaction] = {}
    rentals: list[Rental] = []

    for spec in TRANSACTION_SPECS:
        listing = materials[spec.listing]
        buyer = users[spec.buyer]
        listing_type = ListingType(listing.listing_type)

        rental_start: datetime | None = None
        rental_due: datetime | None = None
        if spec.rental is not None:
            rental_due = now + timedelta(days=spec.rental.due_in_days)
            rental_start = rental_due - timedelta(days=spec.rental.term_days)

        if spec.status == TransactionStatus.COMPLETED:
            # A completed rental's clock is rebased onto the physical handoff,
            # so the two timestamps are the same moment by definition.
            completed_at = (
                rental_start
                if rental_start is not None
                else now - timedelta(days=spec.completed_days_ago or spec.days_ago)
            )
        else:
            completed_at = None

        transaction = Transaction(
            transaction_id=new_id(),
            listing_id=listing.listing_id,
            buyer_id=buyer.user_id,
            seller_id=listing.uploader_id,
            transaction_type=listing_type.value,
            # EXCHANGE and FREE carry no price; the DB CHECK on the listing says
            # the same thing, and a "free" item with a price is a support ticket.
            agreed_price=(
                None
                if listing_type in (ListingType.EXCHANGE, ListingType.FREE)
                else (spec.agreed_price if spec.agreed_price is not None else listing.price)
            ),
            status=spec.status.value,
            transaction_date=now - timedelta(days=spec.days_ago),
            completed_at=completed_at,
        )
        transactions[spec.key] = transaction

        if spec.rental is not None and rental_start is not None and rental_due is not None:
            rentals.append(
                Rental(
                    rental_id=new_id(),
                    transaction_id=transaction.transaction_id,
                    start_date=rental_start,
                    due_date=rental_due,
                    returned=False,
                    last_reminder_stage=spec.rental.stage.value,
                )
            )

    session.add_all(list(transactions.values()))
    # Flush before the rentals: the FK points at a transaction row that has to
    # exist first, and foreign_keys=ON means SQLite checks.
    await session.flush()
    session.add_all(rentals)
    await session.commit()
    return transactions


async def seed_reviews(
    session: AsyncSession, transactions: dict[str, Transaction]
) -> list[Review]:
    """Twelve reviews, then the rating columns recomputed from them.

    Two per completed transaction is the ceiling the schema sets --
    ``UNIQUE (transaction_id, reviewer_id)`` -- so six completed deals give
    twelve, both sides of every one.
    """
    reviews: list[Review] = []
    for spec in REVIEW_SPECS:
        transaction = transactions[spec.transaction]
        if spec.author == "buyer":
            reviewer_id, reviewee_id = transaction.buyer_id, transaction.seller_id
        else:
            reviewer_id, reviewee_id = transaction.seller_id, transaction.buyer_id
        anchor = transaction.completed_at or transaction.transaction_date
        reviews.append(
            Review(
                review_id=new_id(),
                transaction_id=transaction.transaction_id,
                reviewer_id=reviewer_id,
                reviewee_id=reviewee_id,
                listing_id=transaction.listing_id,
                rating=spec.rating,
                comment=spec.comment,
                created_at=anchor + timedelta(hours=spec.hours_after),
            )
        )
    session.add_all(reviews)
    await session.flush()

    for user_id in {review.reviewee_id for review in reviews}:
        await recompute_rating(session, user_id)
    await session.commit()
    return reviews


async def seed_requests(
    session: AsyncSession,
    users: dict[str, User],
    campuses: dict[str, Campus],
    now: datetime,
) -> dict[str, Request]:
    requests = {
        spec.key: Request(
            request_id=new_id(),
            requester_id=users[spec.requester].user_id,
            course_code=spec.course,
            course_code_norm=normalize_course_code(spec.course),
            department=COURSES[spec.course].department,
            edition=spec.edition,
            listing_type=spec.listing_type.value if spec.listing_type else None,
            max_price=spec.max_price,
            campus_id=campuses[spec.campus].campus_id if spec.campus else None,
            description=spec.description or None,
            status=spec.status.value,
            created_at=now - timedelta(days=spec.days_ago),
        )
        for spec in REQUEST_SPECS
    }
    session.add_all(list(requests.values()))
    await session.commit()
    return requests


async def seed_wishlist_items(
    session: AsyncSession,
    users: dict[str, User],
    materials: dict[str, StudyMaterial],
    now: datetime,
    rng: random.Random,
) -> int:
    wishlists = {
        wishlist.user_id: wishlist
        for wishlist in (await session.execute(select(Wishlist))).scalars().all()
    }
    items: list[WishlistItem] = []
    for user_key, listing_keys in WISHLIST_ITEMS.items():
        for listing_key in listing_keys:
            listing = materials[listing_key]
            # A bookmark cannot predate the listing it points at, so the date is
            # drawn from the window between the upload and now rather than from
            # a flat "some time in the last three weeks".
            earliest = listing.upload_date + timedelta(hours=1)
            items.append(
                WishlistItem(
                    wishlist_id=wishlists[users[user_key].user_id].wishlist_id,
                    listing_id=listing.listing_id,
                    added_at=earliest + (now - earliest) * rng.random(),
                )
            )
    session.add_all(items)
    await session.commit()
    return len(items)


async def seed_conversations(
    session: AsyncSession,
    users: dict[str, User],
    materials: dict[str, StudyMaterial],
    now: datetime,
) -> dict[str, Conversation]:
    conversations: dict[str, Conversation] = {}
    messages: list[Message] = []

    for spec in CONVERSATION_SPECS:
        # CHECK (user_a_id < user_b_id): the pair is stored canonically so the
        # UNIQUE index actually prevents a second thread for the same two people.
        first, second = sorted(
            (users[spec.user_one].user_id, users[spec.user_two].user_id)
        )
        started = now - timedelta(days=spec.started_days_ago)
        conversation = Conversation(
            conversation_id=new_id(),
            user_a_id=first,
            user_b_id=second,
            listing_id=materials[spec.listing].listing_id,
            created_at=started,
            last_message_at=started,
        )
        conversations[spec.key] = conversation

        # Spread the thread over the hours since it opened, so the transcript
        # reads as a conversation rather than a burst.
        spacing = timedelta(
            hours=max(0.25, (spec.started_days_ago * 24) / (len(spec.messages) + 1))
        )
        unread_from = len(spec.messages) - spec.unread_tail
        for index, (sender_key, body) in enumerate(spec.messages):
            sent_at = started + spacing * (index + 1)
            messages.append(
                Message(
                    message_id=new_id(),
                    conversation_id=conversation.conversation_id,
                    sender_id=users[sender_key].user_id,
                    content=body,
                    timestamp=sent_at,
                    # The tail stays unread so the inbox badge is not zero on a
                    # freshly seeded machine.
                    read_at=(
                        None if index >= unread_from else sent_at + timedelta(minutes=7)
                    ),
                )
            )
            conversation.last_message_at = sent_at

    session.add_all(list(conversations.values()))
    # Threads before messages: messages.conversation_id is a real FK.
    await session.flush()
    session.add_all(messages)
    await session.commit()
    return conversations


async def seed_reports(
    session: AsyncSession,
    users: dict[str, User],
    materials: dict[str, StudyMaterial],
    now: datetime,
) -> list[Report]:
    reports = [
        Report(
            report_id=new_id(),
            reporter_id=users[spec.reporter].user_id,
            listing_id=materials[spec.listing].listing_id,
            reason=spec.reason.value,
            details=spec.details,
            status=spec.status.value,
            created_at=now - timedelta(days=spec.days_ago),
            resolved_by=(
                users["admin"].user_id if spec.status != ReportStatus.OPEN else None
            ),
            resolved_at=(
                now - timedelta(days=spec.resolved_days_ago)
                if spec.resolved_days_ago is not None
                else None
            ),
            resolution_note=spec.resolution_note,
        )
        for spec in REPORT_SPECS
    ]
    session.add_all(reports)
    await session.commit()
    return reports


async def seed_notifications(
    session: AsyncSession,
    users: dict[str, User],
    materials: dict[str, StudyMaterial],
    transactions: dict[str, Transaction],
    conversations: dict[str, Conversation],
    requests: dict[str, Request],
    now: datetime,
) -> list[Notification]:
    """History for the bell, with unread rows so the badge has a number.

    Every row points at something that exists: a notification whose ref_id
    404s is worse than no notification at all.
    """
    ref_ids = {
        "listing": {key: value.listing_id for key, value in materials.items()},
        "transaction": {key: value.transaction_id for key, value in transactions.items()},
        "conversation": {
            key: value.conversation_id for key, value in conversations.items()
        },
        "request": {key: value.request_id for key, value in requests.items()},
    }
    notifications = [
        Notification(
            notification_id=new_id(),
            user_id=users[spec.user].user_id,
            type=spec.type.value,
            message=spec.message,
            ref_type=spec.ref_type,
            ref_id=ref_ids[spec.ref_type][spec.ref_key],
            is_read=spec.is_read,
            created_at=now - timedelta(days=spec.days_ago),
        )
        for spec in NOTIFICATION_SPECS
    ]
    session.add_all(notifications)
    await session.commit()
    return notifications


async def seed_demand_events(
    session: AsyncSession,
    campuses: dict[str, Campus],
    now: datetime,
    rng: random.Random,
) -> int:
    """~400 events inside the rolling trending window (FR 4.4).

    Everything is placed strictly inside ``trending_window_days`` so the whole
    dataset is visible to the dashboard on the day it is seeded, and skewed
    towards recent days for the hot courses so the ranking looks like a trend
    rather than a uniform sprinkle.
    """
    window = float(settings.trending_window_days)
    campus_ids = [campuses[key].campus_id for key, _, _ in CAMPUS_SPECS]
    events: list[CourseDemandEvent] = []

    for course_code, count, hot in DEMAND_PLAN:
        mix = _HOT_MIX if hot else _BASE_MIX
        types = [event_type for event_type, _ in mix]
        weights = [weight for _, weight in mix]
        norm = normalize_course_code(course_code)

        for _ in range(count):
            age = (
                rng.triangular(0.1, window - 1.0, 4.0)
                if hot
                else rng.uniform(0.1, window - 1.0)
            )
            events.append(
                CourseDemandEvent(
                    course_code_norm=norm,
                    campus_id=rng.choices(campus_ids, weights=_CAMPUS_MIX)[0],
                    event_type=rng.choices(types, weights=weights)[0].value,
                    created_at=now - timedelta(days=age),
                )
            )

    session.add_all(events)
    await session.commit()
    return len(events)


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

_COUNT_TABLES: tuple[tuple[str, type], ...] = (
    ("campuses", Campus),
    ("users", User),
    ("study materials", StudyMaterial),
    ("requests", Request),
    ("wishlists", Wishlist),
    ("wishlist items", WishlistItem),
    ("conversations", Conversation),
    ("messages", Message),
    ("transactions", Transaction),
    ("rentals", Rental),
    ("reviews", Review),
    ("reports", Report),
    ("notifications", Notification),
    ("course demand events", CourseDemandEvent),
)


async def _count(session: AsyncSession, model: type) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(model))).scalar_one()
    )


async def _breakdown(session: AsyncSession, column: Any) -> str:
    """`STATUS n, STATUS n` for one enumerated column, busiest first."""
    stmt = select(column, func.count()).group_by(column).order_by(func.count().desc())
    rows = (await session.execute(stmt)).all()
    return ", ".join(f"{value} {count}" for value, count in rows)


async def print_summary(session: AsyncSession, files_removed: int) -> None:
    print("\n  NoteVault demo data")
    print("  " + "=" * 62)
    for label, model in _COUNT_TABLES:
        total = await _count(session, model)
        print(f"  {label:<26}{total:>6}")
    print("  " + "-" * 62)

    print(f"  listings by status         {await _breakdown(session, StudyMaterial.status)}")
    print(f"  transactions by status     {await _breakdown(session, Transaction.status)}")
    print(f"  requests by status         {await _breakdown(session, Request.status)}")
    print(f"  reports by status          {await _breakdown(session, Report.status)}")

    unread = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.is_read.is_(False))
            )
        ).scalar_one()
    )
    print(f"  unread notifications       {unread}")

    # Read the two dashboards back through the services that serve them, so the
    # summary is a verification rather than a restatement of the fixtures.
    analytics.invalidate()
    trending = await analytics.trending_courses(session, limit=3)
    print(
        "  trending top 3             "
        + ", ".join(f"{row['course_code']} {row['demand_score']:.1f}" for row in trending)
    )
    leaders = await analytics.top_contributors(session, limit=3)
    print(
        "  leaderboard top 3          "
        + ", ".join(f"{row['name']} {row['score']:.1f}" for row in leaders)
    )
    print(
        "  rental reminders ready     T-3, T-1 and OVERDUE (one rental each, "
        "unreturned)"
    )
    if files_removed:
        print(f"  files removed on reset     {files_removed}")

    print("\n  Demo accounts")
    print("  " + "-" * 62)
    print(f"  {'admin':<10}{ADMIN_SPEC[2]:<32}{ADMIN_PASSWORD}")
    print(f"  {'student':<10}{STUDENT_SPECS[0][2]:<32}{STUDENT_PASSWORD}")
    print(
        f"  {'student':<10}"
        f"{'student2 ... student11@notevault.test':<32}{STUDENT_PASSWORD}"
    )
    print("\n  Start the app and open http://localhost:8000\n")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seed.py",
        description=(
            "Write the NoteVault demo dataset. Without --reset the script "
            "refuses to touch a database that already has users."
        ),
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete every existing row and its stored files first, then reseed",
    )
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()
    settings.ensure_storage_dirs()
    rng = random.Random(RANDOM_SEED)
    # One "now" for the whole run, so every relative date in the dataset is
    # measured from the same instant and the rental buckets cannot straddle a
    # midnight that passed halfway through seeding.
    now = utcnow()

    try:
        async with SessionLocal() as session:
            existing = await _count(session, User)
            if existing and not args.reset:
                print(
                    f"\n  Refusing to seed: {existing} users already exist.\n"
                    "  Re-run with --reset to delete everything and start over.\n",
                    file=sys.stderr,
                )
                return 1

            files_removed = await reset(session) if args.reset else 0

            campuses = await seed_campuses(session)
            users = await seed_users(session, campuses, now)
            materials = await seed_materials(session, users, now, rng)
            transactions = await seed_transactions(session, users, materials, now)
            await seed_reviews(session, transactions)
            requests = await seed_requests(session, users, campuses, now)
            await seed_wishlist_items(session, users, materials, now, rng)
            conversations = await seed_conversations(session, users, materials, now)
            await seed_reports(session, users, materials, now)
            await seed_notifications(
                session, users, materials, transactions, conversations, requests, now
            )
            await seed_demand_events(session, campuses, now, rng)

            await print_summary(session, files_removed)
    finally:
        # aiosqlite keeps a thread per pooled connection; without this the
        # interpreter exits on a warning instead of cleanly.
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
