from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from .forms import TicketStatusForm

from .forms import AttachmentForm, CommentForm, ProfileForm, SignupForm, TicketForm
from .models import Attachment, Profile, Ticket


def ensure_profile(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile

def is_moderator_or_admin(user):
    profile = ensure_profile(user)
    return profile.role in [Profile.ROLE_MODERATOR, Profile.ROLE_ADMIN]


def can_view_ticket(user, ticket):
    if not ticket.is_private:
        return True

    if ticket.owner_id == user.id:
        return True

    return is_moderator_or_admin(user)

def can_edit_ticket(user, ticket):
    if ticket.owner_id == user.id:
        return True

    return is_moderator_or_admin(user)

def is_admin(user):
    profile = ensure_profile(user)
    return profile.role == Profile.ROLE_ADMIN


def can_delete_ticket(user, ticket):
    if ticket.owner_id == user.id:
        return True

    return is_admin(user)

def accessible_tickets_for(user):
    profile = ensure_profile(user)

    if profile.role in [Profile.ROLE_MODERATOR, Profile.ROLE_ADMIN]:
        return Ticket.objects.all()

    return Ticket.objects.filter(
        Q(is_private=False) | Q(owner=user)
    )

def index(request):
    if request.user.is_authenticated:
        return redirect('ticket_list')
    return redirect('login')


def signup(request):
    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            Profile.objects.create(user=user)
            login(request, user)
            messages.success(request, 'Аккаунт создан')
            return redirect('ticket_list')
    else:
        form = SignupForm()
    return render(request, 'tickets/signup.html', {'form': form})


@login_required
def ticket_list(request):
    ensure_profile(request.user)

    qs = accessible_tickets_for(request.user).select_related('owner')

    status = request.GET.get('status')
    if status:
        qs = qs.filter(status=status)

    paginator = Paginator(qs, 5)
    page_number = request.GET.get('page', '1')
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(paginator.num_pages)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    return render(request, 'tickets/ticket_list.html', {'page_obj': page_obj, 'status': status})


@login_required
def ticket_search(request):
    ensure_profile(request.user)

    q = request.GET.get('q', '').strip()
    results = Ticket.objects.none()

    if q:
        results = accessible_tickets_for(request.user).filter(
            Q(title__icontains=q) | Q(description__icontains=q)
        ).select_related('owner').order_by('-created_at')

    return render(request, 'tickets/search.html', {'q': q, 'results': results})


@login_required
def ticket_detail(request, pk):
    ticket = get_object_or_404(
        Ticket.objects.select_related('owner').prefetch_related('comments__author', 'attachments'),
        pk=pk,
    )

    if not can_view_ticket(request.user, ticket):
        raise PermissionDenied

    comment_form = CommentForm()
    attachment_form = AttachmentForm()
    return render(
        request,
        'tickets/ticket_detail.html',
        {'ticket': ticket, 'comment_form': comment_form, 'attachment_form': attachment_form},
    )


@login_required
def ticket_create(request):
    if request.method == 'POST':
        form = TicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.owner = request.user
            ticket.save()
            messages.success(request, 'Заявка создана')
            return redirect('ticket_detail', pk=ticket.pk)
    else:
        form = TicketForm()
    return render(request, 'tickets/ticket_form.html', {'form': form, 'mode': 'create'})



@login_required
def ticket_edit(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)

    # Проверка прав редактирования
    if not can_edit_ticket(request.user, ticket):
        raise PermissionDenied

    # Форма обычных полей заявки
    if request.method == 'POST':
        form = TicketForm(request.POST, instance=ticket)

        # Форма статуса для модераторов/админов
        status_form = None
        if is_moderator_or_admin(request.user):
            status_form = TicketStatusForm(request.POST, instance=ticket)

        # Сохраняем обе формы
        if form.is_valid() and (status_form is None or status_form.is_valid()):
            form.save()
            if status_form:
                status_form.save()
            messages.success(request, 'Заявка обновлена')
            return redirect('ticket_detail', pk=ticket.pk)
    else:
        form = TicketForm(instance=ticket)
        status_form = None
        if is_moderator_or_admin(request.user):
            status_form = TicketStatusForm(instance=ticket)

    return render(
        request,
        'tickets/ticket_form.html',
        {
            'form': form,
            'mode': 'edit',
            'ticket': ticket,
            'status_form': status_form  # шаблон будет проверять, если нужно отображать поле статуса
        }
    )

@login_required
@require_POST
def ticket_delete(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)

    if not can_delete_ticket(request.user, ticket):
        raise PermissionDenied

    ticket.delete()
    messages.success(request, 'Заявка удалена')
    return redirect('ticket_list')


@login_required
def comment_add(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if request.method != 'POST':
        return redirect('ticket_detail', pk=pk)

    form = CommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.ticket = ticket
        comment.author = request.user
        comment.save()
        messages.success(request, 'Комментарий добавлен')
    return redirect('ticket_detail', pk=pk)


@login_required
def attachment_upload(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if request.method != 'POST':
        return redirect('ticket_detail', pk=pk)

    form = AttachmentForm(request.POST, request.FILES)
    if form.is_valid():
        attachment = form.save(commit=False)
        attachment.ticket = ticket
        attachment.uploaded_by = request.user
        attachment.original_name = request.FILES['file'].name
        attachment.save()
        messages.success(request, 'Файл загружен')
    return redirect('ticket_detail', pk=pk)


@login_required
def attachment_download(request, attachment_id):
    attachment = get_object_or_404(Attachment.objects.select_related('ticket'), pk=attachment_id)
    try:
        return FileResponse(attachment.file.open('rb'), as_attachment=True, filename=attachment.original_name)
    except FileNotFoundError:
        raise Http404('Файл не найден')


@login_required
def profile_edit(request):
    profile = ensure_profile(request.user)
    if request.method == 'POST':
        form = ProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Профиль обновлен')
            return redirect('profile_edit')
    else:
        form = ProfileForm(instance=profile)
    return render(request, 'tickets/profile.html', {'form': form, 'profile': profile})


@login_required
def admin_console(request):
    profile = ensure_profile(request.user)
    if profile.role != Profile.ROLE_ADMIN:
        messages.error(request, 'Требуется роль администратора')
        return redirect('ticket_list')

    users = User.objects.all()
    tickets = Ticket.objects.all()
    return render(request, 'tickets/admin_console.html', {'users': users, 'tickets': tickets})


@login_required
def diagnostics(request):
    data = {
        'DEBUG': settings.DEBUG,
        'SECRET_KEY': settings.SECRET_KEY,
        'ALLOWED_HOSTS': settings.ALLOWED_HOSTS,
        'MEDIA_ROOT': settings.MEDIA_ROOT,
    }
    return render(request, 'tickets/diagnostics.html', {'data': data})


@login_required
def go_next(request):
    return redirect(request.GET.get('next', '/tickets/'))
