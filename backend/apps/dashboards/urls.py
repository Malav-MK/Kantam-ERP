from django.urls import path

from . import views

urlpatterns = [
    path("owner", views.OwnerOverview.as_view()),
    path("machines", views.MachineBoard.as_view()),
    path("tv", views.TvView.as_view()),
    path("gate", views.GateOverview.as_view()),
    path("stores", views.StoresDaily.as_view()),
    path("inventory", views.StockInventory.as_view()),
    path("shortages", views.Shortages.as_view()),
    path("planning", views.PlanningBoard.as_view()),
    path("labels", views.LabelStore.as_view()),
    path("tasks", views.TaskAnalytics.as_view()),
    path("matching/<uuid:product_id>", views.Matching.as_view()),
    path("trace", views.Trace.as_view()),
]
